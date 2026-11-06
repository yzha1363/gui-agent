"""CPU regressions for accumulation, validation isolation and checkpoint recovery."""

from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import torch
import torch.nn.functional as F

from gui_training.trainer import (EpochSampler, VisualExamples, config_hash, inspect_checkpoint,
    load_checkpoint, lr_factor, save_checkpoint, token_count, update_window, validate_cursor,
    validate_training_config, validation_loss, windows)


class CPUAccelerator:
    device = torch.device('cpu')
    def autocast(self): return nullcontext()
    def backward(self, loss): loss.backward()
    def clip_grad_norm_(self, parameters, maximum): return torch.nn.utils.clip_grad_norm_(parameters, maximum)


class ToyLM(torch.nn.Module):
    def __init__(self, dropout=0):
        super().__init__(); self.embedding = torch.nn.Embedding(16, 6); self.drop = torch.nn.Dropout(dropout); self.head = torch.nn.Linear(6, 16)
    def forward(self, input_ids, labels, **kwargs):
        logits = self.head(self.drop(self.embedding(input_ids)))
        loss = F.cross_entropy(logits[:, :-1].reshape(-1, 16), labels[:, 1:].reshape(-1), ignore_index=-100)
        return SimpleNamespace(loss=loss)


def example(values, identifier='example', split='train'):
    ids = torch.tensor([values]); labels = ids.clone(); labels[:, 0] = -100
    return {'id': identifier, 'split': split, 'batch': {'input_ids': ids, 'labels': labels}}


class AccumulationTests(unittest.TestCase):
    def setUp(self): torch.manual_seed(5)

    def test_unequal_token_lengths_match_combined_token_mean_gradient(self):
        model = ToyLM(); reference = deepcopy(model); items = [example([1, 2, 3]), example([1, 4, 5, 6, 7])]
        optimizer = torch.optim.SGD(model.parameters(), lr=.1); other = torch.optim.SGD(reference.parameters(), lr=.1)
        row = update_window(model, optimizer, items, CPUAccelerator(), 1e6)
        losses = [reference(**x['batch']).loss for x in items]; counts = [token_count(x['batch']) for x in items]
        loss = sum(v * n for v, n in zip(losses, counts)) / sum(counts); loss.backward(); other.step()
        self.assertTrue(all(torch.allclose(a, b, atol=1e-7) for a, b in zip(model.parameters(), reference.parameters())))
        self.assertEqual(row['supervised_tokens'], 6)

    def test_short_last_window_is_not_divided_by_configured_accumulation(self):
        sizes = [len(x) for x in windows(range(5), 2)]
        self.assertEqual(sizes, [2, 2, 1])
        model = ToyLM(); reference = deepcopy(model); item = example([1, 2, 3])
        opt = torch.optim.SGD(model.parameters(), lr=.1); other = torch.optim.SGD(reference.parameters(), lr=.1)
        update_window(model, opt, [item], CPUAccelerator(), 1e6)
        reference(**item['batch']).loss.backward(); other.step()
        self.assertTrue(all(torch.allclose(a, b, atol=1e-7) for a, b in zip(model.parameters(), reference.parameters())))

    def test_nonfinite_loss_stops_before_optimizer_update(self):
        model = ToyLM(); before = deepcopy(model.state_dict()); opt = torch.optim.SGD(model.parameters(), lr=.1)
        def invalid(**kwargs): return SimpleNamespace(loss=sum(p.sum() for p in model.parameters()) * float('nan'))
        with patch.object(model, 'forward', side_effect=invalid), self.assertRaises(RuntimeError):
            update_window(model, opt, [example([1, 2])], CPUAccelerator(), 1)
        self.assertTrue(all(torch.equal(before[k], v) for k, v in model.state_dict().items()))

    def test_frozen_weights_remain_without_gradients(self):
        model = ToyLM(); model.embedding.requires_grad_(False)
        row = update_window(model, torch.optim.SGD(model.head.parameters(), lr=.1), [example([1, 2, 3])], CPUAccelerator(), 1)
        self.assertGreater(row['gradient_norm_before_clip'], 0); self.assertIsNone(model.embedding.weight.grad)

    def test_no_supervised_tokens_is_rejected(self):
        batch = example([1, 2])['batch']; batch['labels'].fill_(-100)
        with self.assertRaises(ValueError): token_count(batch)


class SamplerAndValidationTests(unittest.TestCase):
    def test_resume_offset_preserves_remaining_epoch_order(self):
        full = list(EpochSampler(8, 42, 1)); resumed = list(EpochSampler(8, 42, 1, 2))
        self.assertEqual(full[2:], resumed); self.assertEqual(sorted(full), list(range(8)))

    def test_sampler_does_not_consume_global_random_state(self):
        random.seed(8); before = random.getstate(); list(EpochSampler(8, 42, 2))
        self.assertEqual(before, random.getstate())

    def test_validation_uses_token_weighting_and_restores_training_mode(self):
        model = ToyLM(); model.train(); items = [example([1, 2, 3], split='validation'), example([1, 4, 5, 6, 7], split='validation')]
        before = deepcopy(model.state_dict()); result = validation_loss(model, items, CPUAccelerator())
        expected = sum(float(model(**x['batch']).loss.detach()) * token_count(x['batch']) for x in items) / 6
        self.assertAlmostEqual(result['loss'], expected, places=6); self.assertTrue(model.training)
        self.assertTrue(all(p.grad is None for p in model.parameters()))
        self.assertTrue(all(torch.equal(before[k], v) for k, v in model.state_dict().items()))

    def test_fixed_test_split_cannot_enter_model_dataset(self):
        with self.assertRaises(ValueError): VisualExamples('does-not-exist', 'test', None, 1024)

    def test_invalid_cursor_cannot_skip_or_replay_half_an_update(self):
        self.assertEqual(validate_cursor({'epoch': 1, 'offset': 2, 'global_step': 5}, 8, 2, 3), (1, 2, 5))
        for cursor in [{'epoch': 1, 'offset': 1, 'global_step': 5}, {'epoch': 3, 'offset': 2, 'global_step': 13},
                       {'epoch': 0, 'offset': 4, 'global_step': 1}]:
            with self.subTest(cursor=cursor), self.assertRaises(ValueError): validate_cursor(cursor, 8, 2, 3)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        random.seed(11); np.random.seed(11); torch.manual_seed(11)
        self.model = ToyLM(.25); self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=.01)
        self.scheduler = torch.optim.lr_scheduler.LambdaLR(self.optimizer, lambda step: lr_factor(step, 0, 4))
        update_window(self.model, self.optimizer, [example([1, 2, 3])], CPUAccelerator(), 1); self.scheduler.step()

    def export(self, path):
        path.mkdir(); torch.save(self.model.state_dict(), path / 'toy.pt')

    def checkpoint(self):
        state = {'config_sha256': 'config', 'dataset_sha256': 'dataset', 'cursor': {'epoch': 0, 'offset': 1, 'global_step': 1}}
        path = self.root / 'checkpoint'; save_checkpoint(path, state, self.export, self.optimizer, self.scheduler)
        return path

    def test_roundtrip_restores_optimizer_scheduler_and_all_rngs(self):
        path = self.checkpoint(); state = inspect_checkpoint(path, 'config', 'dataset')
        expected = (random.random(), float(np.random.random()), torch.rand(4)); expected_lr = self.scheduler.get_last_lr()
        other = ToyLM(.25); opt = torch.optim.AdamW(other.parameters(), lr=.9); sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda step: lr_factor(step, 0, 4))
        load_checkpoint(path, state, lambda p: other.load_state_dict(torch.load(p / 'toy.pt', weights_only=True)), opt, sched)
        self.assertEqual(expected[0], random.random()); self.assertEqual(expected[1], float(np.random.random()))
        self.assertTrue(torch.equal(expected[2], torch.rand(4))); self.assertEqual(expected_lr, sched.get_last_lr())
        self.assertEqual(self.optimizer.param_groups[0]['lr'], opt.param_groups[0]['lr'])

    def test_resume_with_dropout_matches_uninterrupted_next_update(self):
        path = self.checkpoint(); state = inspect_checkpoint(path, 'config', 'dataset')
        item = example([1, 3, 5, 7])
        expected_loss = update_window(self.model, self.optimizer, [item], CPUAccelerator(), 1)['loss']; self.scheduler.step()
        final = deepcopy(self.model.state_dict())
        other = ToyLM(.25); opt = torch.optim.AdamW(other.parameters(), lr=.01); sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda step: lr_factor(step, 0, 4))
        load_checkpoint(path, state, lambda p: other.load_state_dict(torch.load(p / 'toy.pt', weights_only=True)), opt, sched)
        actual_loss = update_window(other, opt, [item], CPUAccelerator(), 1)['loss']; sched.step()
        self.assertEqual(expected_loss, actual_loss)
        self.assertTrue(all(torch.equal(final[k], v) for k, v in other.state_dict().items()))

    def test_changed_dataset_and_config_are_rejected(self):
        path = self.checkpoint()
        for config, dataset in [('different', 'dataset'), ('config', 'different')]:
            with self.subTest(config=config, dataset=dataset), self.assertRaises(ValueError): inspect_checkpoint(path, config, dataset)

    def test_tampered_optimizer_is_rejected_before_deserialization(self):
        path = self.checkpoint(); (path / 'optimizer.pt').write_bytes(b'changed')
        with patch('torch.load', side_effect=AssertionError('must not deserialize')), self.assertRaises(ValueError):
            inspect_checkpoint(path, 'config', 'dataset')

    def test_missing_checkpoint_file_is_not_accepted(self):
        path = self.checkpoint(); (path / 'state.json').unlink()
        with self.assertRaises(ValueError): inspect_checkpoint(path, 'config', 'dataset')

    def test_existing_checkpoint_cannot_be_overwritten(self):
        path = self.checkpoint()
        with self.assertRaises(FileExistsError): save_checkpoint(path, {}, self.export, self.optimizer, self.scheduler)


class TrainingConfigTests(unittest.TestCase):
    def test_nan_boolean_and_unsupported_batches_are_rejected(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'configs/week3-tuesday.json').read_text())
        validate_training_config(config)
        for edit in [{'learning_rate': float('nan')}, {'epochs': True}, {'batch_size': 2}, {'device': 'cpu'}]:
            with self.subTest(edit=edit), self.assertRaises(ValueError): validate_training_config({**config, **edit})

    def test_config_fingerprint_changes_with_training_hyperparameters(self):
        self.assertNotEqual(config_hash({'epochs': 2}), config_hash({'epochs': 3}))


if __name__ == '__main__': unittest.main()

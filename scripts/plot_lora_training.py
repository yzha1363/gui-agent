"""Optional offline figure helper; requires reportlab and pypdfium2, not used by training."""

import argparse
import csv
import json
import math
from pathlib import Path
import tempfile

from reportlab.graphics import renderPDF, renderSVG
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib.colors import HexColor
import pypdfium2 as pdfium


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    read = lambda p: [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
    train, validation = read(args.run / 'train.jsonl'), read(args.run / 'validation.jsonl')
    summary = json.loads((args.run / 'summary.json').read_text(encoding='utf-8'))
    drawing = Drawing(960, 560)
    drawing.add(String(70, 525, 'LoRA training and validation loss', fontName='Helvetica-Bold', fontSize=23))
    drawing.add(String(70, 500, f'{summary["epochs_completed"]} epochs / {summary["training_examples"]} training examples / '
                       f'{summary["global_step"]} optimizer updates / fixed test unused', fontSize=13))
    plot = LinePlot(); plot.x, plot.y, plot.width, plot.height = 80, 110, 800, 330
    plot.data = [[(r['step'], r['loss']) for r in train], [(r['step'], r['loss']) for r in validation]]
    plot.xValueAxis.valueMin = 0; plot.xValueAxis.valueMax = max(r['step'] for r in train); plot.xValueAxis.valueStep = 2
    values = [r['loss'] for r in train + validation]
    plot.yValueAxis.valueMin = math.floor(min(values) * 4) / 4; plot.yValueAxis.valueMax = math.ceil(max(values) * 4) / 4
    plot.yValueAxis.valueStep = .25; plot.yValueAxis.labelTextFormat = '%.2f'
    colors = [HexColor('#2563eb'), HexColor('#d97706')]
    for i, color in enumerate(colors):
        plot.lines[i].strokeColor = color; plot.lines[i].strokeWidth = 2
        plot.lines[i].symbol = makeMarker('FilledCircle'); plot.lines[i].symbol.size = 5
        plot.lines[i].symbol.fillColor = color; plot.lines[i].symbol.strokeColor = color
    drawing.add(plot)
    drawing.add(String(80, 450, 'Token cross-entropy loss', fontSize=12))
    drawing.add(String(480, 65, 'Optimizer update', fontSize=13, textAnchor='middle'))
    for x, label, color in [(80, 'Train: token-weighted update window', colors[0]), (510, 'Validation: two examples, teacher forced', colors[1])]:
        drawing.add(Line(x, 478, x + 25, 478, strokeColor=color, strokeWidth=3))
        drawing.add(String(x + 33, 474, label, fontSize=12))
    drawing.add(String(70, 28, 'Each training point uses different examples. A lower loss is not a GUI task accuracy result.', fontSize=11))
    renderSVG.drawToFile(drawing, str(args.run / 'loss-curve.svg'))
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / 'curve.pdf'; renderPDF.drawToFile(drawing, str(pdf))
        document = pdfium.PdfDocument(str(pdf)); page = document[0]
        bitmap = page.render(scale=1.8); bitmap.to_pil().save(args.run / 'loss-curve.png')
        bitmap.close(); page.close(); document.close()
    with (args.run / 'metrics.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream); writer.writerow(['split', 'step', 'epoch', 'loss', 'supervised_tokens'])
        for name, rows in [('train', train), ('validation', validation)]:
            for row in rows: writer.writerow([name, row['step'], row['epoch'], row['loss'], row['supervised_tokens']])
    print(args.run / 'loss-curve.png')


if __name__ == '__main__': main()

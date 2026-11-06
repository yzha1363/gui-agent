"""Deterministic post-commit fault injection; only polls an independent callback."""

import time


def inject_after_submit_acknowledgement(before, read_count, timeout=2, clock=time.monotonic, sleep=time.sleep):
    deadline=clock()+timeout
    while clock()<deadline:
        count=read_count()
        if type(count) is int and count==before+1:
            raise RuntimeError('Injected failure after acknowledged real submit callback.')
        if type(count) is int and count>before+1:
            raise AssertionError('More than one submit callback; cannot claim single-commit injection.')
        sleep(min(.025,max(0,deadline-clock())))
    raise AssertionError('Submit callback not acknowledged; post-commit fault was not injected.')

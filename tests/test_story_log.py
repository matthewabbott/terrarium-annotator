"""L0 tests for the story log + merge tree, per docs/plan T2 and
docs/design/dev-verification.md. Merge functions are injected fakes —
no LLM anywhere in this layer.
"""

from __future__ import annotations

import pytest

from terrarium_annotator.memory import StoryLog


@pytest.fixture
def log():
    with StoryLog(":memory:") as log:
        yield log


def fill(log: StoryLog, n: int, thread_id: int = 1) -> None:
    for i in range(n):
        log.append(thread_id, f"entry {i}")


def settle_all(log: StoryLog) -> None:
    while log.pending():
        lo, hi = log.pending()[0]
        log.settle(lo, hi, f"summary of {lo}-{hi}")


class TestLog:
    def test_append_assigns_sequential_seqs(self, log):
        assert log.append(1, "first") == 0
        assert log.append(1, "second") == 1
        assert log.log_len() == 2
        assert log.entry(1).gist == "second"

    def test_rejects_multiline_or_empty_gist(self, log):
        with pytest.raises(ValueError):
            log.append(1, "")
        with pytest.raises(ValueError):
            log.append(1, "two\nlines")

    def test_persistence(self, tmp_path):
        db = tmp_path / "mem.db"
        with StoryLog(db) as log:
            fill(log, 3)
            log.close_thread(1)
            settle_all(log)
        with StoryLog(db) as log:
            assert log.log_len() == 3
            assert log.pending() == []
            assert log._settled(0, 2) == "summary of 0-2"


class TestPending:
    def test_aligned_power_of_two_smallest_first(self, log):
        fill(log, 8)
        log.close_thread(1)
        pending = log.pending()
        assert (0, 2) in pending and (2, 4) in pending
        assert (0, 4) in pending and (0, 8) in pending
        assert pending[0] == (0, 2)
        for lo, hi in pending:
            size = hi - lo
            assert size & (size - 1) == 0 and lo % size == 0

    def test_partial_blocks_not_offered(self, log):
        fill(log, 3)  # block (2,4) incomplete; (0,2) only
        log.close_thread(1)
        assert log.pending() == [(0, 2)]

    def test_open_thread_blocks_settlement(self, log):
        log.append(1, "a0")  # thread 1: entries 0-1
        log.append(1, "a1")
        log.append(2, "b0")  # thread 2: entries 2-3
        log.append(2, "b1")
        log.close_thread(2)
        # (2,4) is closed; (0,2) is in open thread 1; (0,4) straddles.
        assert log.pending() == [(2, 4)]
        log.close_thread(1)
        assert log.pending() == [(0, 2), (2, 4), (0, 4)]

    def test_strict_in_order_settlement(self, log):
        fill(log, 4)
        log.close_thread(1)
        with pytest.raises(ValueError, match="in order"):
            log.settle(2, 4, "out of order")
        log.settle(0, 2, "ok")
        assert log.pending()[0] == (2, 4)


class TestCover:
    @pytest.mark.parametrize(
        "T,budget", [(1, 4), (7, 4), (16, 8), (33, 8), (100, 10), (64, 64)]
    )
    def test_budget_respected_when_settled(self, log, T, budget):
        fill(log, T)
        log.close_thread(1)
        settle_all(log)
        items = log.cover(budget)
        assert len(items) <= budget
        assert items[0].lo == 0 and items[-1].hi == T  # full span

    def test_recency_gradient(self, log):
        fill(log, 32)
        log.close_thread(1)
        settle_all(log)
        items = log.cover(8)
        # Oldest item covers at least as much as the newest (age decay).
        first_span = items[0].hi - items[0].lo
        last_span = items[-1].hi - items[-1].lo
        assert first_span >= last_span
        assert items[-1].kind == "raw"  # newest stays verbatim

    def test_unsettled_blocks_fall_back_to_raw(self, log):
        fill(log, 8)  # thread open: nothing settled
        items = log.cover(4)  # tiling wants summaries; we expand instead
        assert all(i.kind == "raw" for i in items)
        assert [i.text for i in items] == [f"entry {i}" for i in range(8)]
        assert len(items) == 8  # over budget: documented fallback behavior

    def test_empty_log(self, log):
        assert log.cover(10) == []

    def test_rejects_bad_budget(self, log):
        with pytest.raises(ValueError):
            log.cover(0)


class TestForget:
    def test_drops_block_ancestors_and_later_siblings(self, log):
        fill(log, 8)
        log.close_thread(1)
        settle_all(log)
        assert log.tree_version == 0
        dropped = log.forget(2, 4)
        assert (2, 4) in dropped and (0, 4) in dropped and (0, 8) in dropped
        assert (0, 2) not in dropped  # untouched earlier block
        assert log.tree_version == 1
        assert log._settled(0, 2) is not None
        # Rebuild: pending offers the dropped blocks again.
        assert (2, 4) in log.pending()

    def test_log_untouched_by_forget(self, log):
        fill(log, 4)
        log.close_thread(1)
        settle_all(log)
        log.forget(0, 2)
        assert log.log_len() == 4

    def test_rejects_misaligned_block(self, log):
        fill(log, 4)
        with pytest.raises(ValueError):
            log.forget(1, 3)

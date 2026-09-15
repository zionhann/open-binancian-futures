import multiprocessing
import os
import sqlite3

import pytest

from open_binancian_futures.live_journal import OrderJournal
from open_binancian_futures.models import OrderIntent
from open_binancian_futures.types import OrderType, PositionSide

INTENT = OrderIntent('BTCUSDT', PositionSide.BUY, OrderType.LIMIT, 100, 1)


def child_lock(path, pipe):
    journal = OrderJournal(path, 'identity')
    journal.open()
    pipe.send(True)
    pipe.recv()
    os._exit(0)


def test_durable_intent_identity_and_restart(tmp_path):
    path = tmp_path / 'orders.sqlite3'
    journal = OrderJournal(path, 'identity')
    journal.open()
    identifier = journal.prepare(INTENT, 10)
    assert journal.pending()[0].state == 'prepared'
    journal.close()
    other = OrderJournal(path, 'other-account')
    with pytest.raises(ValueError, match='identity'):
        other.open()
    journal.open()
    record = journal.pending()[0]
    assert record.client_order_id == identifier and record.intent == INTENT
    journal.update(identifier, 'rejected')
    assert journal.pending() == []
    journal.close()
    journal.close()


def test_real_process_lock_and_crash_unlock(tmp_path):
    path = tmp_path / 'orders.sqlite3'
    parent, child = multiprocessing.Pipe()
    process = multiprocessing.Process(target=child_lock, args=(path, child))
    process.start()
    assert parent.recv()
    journal = OrderJournal(path, 'identity')
    with pytest.raises(RuntimeError, match='already running'):
        journal.open()
    parent.send(True)
    process.join(5)
    assert process.exitcode == 0
    journal.open()
    journal.close()


def test_storage_failure_no_prepared_record(tmp_path):
    journal = OrderJournal(tmp_path / 'orders.sqlite3', 'identity')
    journal.open()
    journal.connection.execute('PRAGMA query_only=ON')
    with pytest.raises(sqlite3.OperationalError):
        journal.prepare(INTENT, 10)
    assert not journal.pending()
    journal.close()

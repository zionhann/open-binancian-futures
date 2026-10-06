# Issue #36 acceptance coverage

The two PRs cover the framework requirements in
[issue #36](https://github.com/zionhann/open-binancian-futures/issues/36).
PR A is [#37](https://github.com/zionhann/open-binancian-futures/pull/37)
(pre-dispatch waiting, lifecycle logging, startup leverage). PR B contains only the
subsequent account-event/journal/REST changes and is based on PR A. Merge A before B.
The issue remains open until both are reviewed and merged.

Strategy signals and stop rules remain unchanged. A candle signal is valid until
the next scheduled/received close; framework exposure/margin checks are repeated
without replaying the strategy or adding a signal-validation callback. Process
startup applies configured leverage only without adopted exposure; reconnect and
later entries preserve actual leverage. Existing positions and entry orders are
never changed to make initialization succeed.

| Issue acceptance item | Scope | Executable evidence |
|---|---|---|
| Startup leverage is prepared before activation | A | `test_startup_sets_leverage_only_without_existing_exposure`, `test_startup_leverage_failure_retries_before_activation` |
| Manual leverage survives entry/reconnect and sizes with actual value | A+B | startup/manual-reconnect test above; `test_leverage_config_is_direct_and_multi_asset_mode_blocks_entries`; existing actual-leverage sizing/stop tests |
| Complete normal events update state/journal without full reads | B | `test_account_partial_updates_keep_other_state_and_do_not_use_wallet_as_available`, `test_known_receipt_and_event_resolve_journal_without_order_queries` |
| Cross-domain arrival order, partial fills, reversed/duplicate events and reservations remain consistent | B | `test_fill_callback_observes_position_and_protects_without_full_reads`, `test_partial_lookup_without_execution_details_keeps_fresh_remaining_quantity`, equal-time position and unknown/cancel tests |
| A → B → A does not add reads/hooks | B | `test_interleaved_duplicates_and_reversed_status_do_not_repeat_reads_or_hooks` |
| Pending entry waits without deadlock and sends once after validation | A | `test_pending_refresh_resumes_same_decision_with_latest_balance`, `test_order_hook_entry_does_not_wait_for_its_own_user_consumer`, dispatch-slot event/price tests |
| Expiry/shutdown/recovery/conflicting position/order cancels old decisions | A | `test_suspended_entry_cancels_on_expiry_or_conflict`, `test_shutdown_logs_cancelled_wait_and_drains_external_submission`, pre-disconnect decision regressions |
| Lost placement/cancel response and restart never resend | A+B | `test_uncertain_placement_and_cancel_keep_original_id_and_reservations`; existing `test_accepted_timeout_restart_query_no_resend` and cancelled-placement drain tests |
| Fill protection uses consistent positions; rejection/trigger failure is visible | B | fill-order tests above; `test_missing_fill_position_reads_only_affected_symbol`, `test_risk_events_notify_and_stop_entries_while_allowing_protection`, `test_rejected_protection_receipt_holds_new_risk_and_notifies` |
| Wallet/cross-wallet never replace free balance; no double reservation | B | partial-account test above; `test_balance_refresh_does_not_deduct_accepted_reservation_twice`, known-cancel and uncertain-cancel tests |
| Configuration/mode, untracked margin impact and funding are handled | B | `test_existing_multi_assets_mode_blocks_entries_without_changing_mode`, `test_single_asset_event_does_not_clear_an_unsupported_position_mode_hold`, `test_untracked_orders_and_funding_share_one_free_balance_read` |
| REST delay/failure, event gaps, reconnect and periodic safety reconciliation remain safe | A+B | receive-fence tests, existing REST offload/recovery/protection suites; `test_watch_only_reconciles_uncertain_orders_every_15_seconds` |
| Backtests unchanged; fake adapter proves fewer event reads | A+B | full pytest suite including causality/accounting/cache tests; complete-event tests assert zero full/order queries and one coalesced balance read |

Additional ordering boundaries: terminal receipts reject delayed NEW updates;
different updates in one millisecond are applied; ambiguous same-time replay reads
only the affected entity. REST fences exclude cached leverage, mode or orders if
the read did not query them. Actual algo-child execution resolves its parent even
when execution arrives before linkage. Trigger alone never fabricates a fill.
`TRADE_LITE` is ignored so it cannot duplicate ordinary realized-profit accounting.
Order namespace follows the event/REST source, including regular liquidation orders.
Conditional limit price and trigger price remain separate and participate in exposure checks.
Invalid position sides, trigger prices and timestamps are rejected before profit mutation.
An authoritative flat-position read clears the protection hold; a balance-only read cannot.
Untracked position-only events also invalidate account-wide free balance.
These boundaries are covered by `tests/test_live_review_merge.py`.

Run the focused cases with:

```sh
python -m pytest -q tests/test_live_entry_wait.py tests/test_live_account_events.py \
  tests/test_live_sync.py tests/test_live_rest_offload.py tests/test_live_event_workers.py \
  tests/test_live_protection_recovery.py
```

All evidence uses deterministic adapters, SDK response/model checks and repository
regressions. No authenticated mainnet/testnet order or deployed strategy was exercised.
That operational verification remains separate from the code acceptance coverage.

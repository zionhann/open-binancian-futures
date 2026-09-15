# 신뢰도 개선 검증 기록

구현 기준은 `737fbc9`이며, 승인된 [의도·인수기준](superpowers/specs/2026-09-15-framework-reliability-design.md)에 따라 작업 1–5를 진행했다. 사용자 전략은 수정하거나 커밋하지 않았다. 실제 계정 주문, 테스트넷 주문, 배포, PR 병합은 수행하지 않았다.

## 작업별 전달

아래 PR은 앞선 PR의 브랜치를 기준으로 하는 의존 PR이다. 1번부터 순서대로 검토·병합해야 하며, 선행 PR 병합 후 후속 PR의 기준 브랜치와 차이를 다시 확인해야 한다.

| 순서 | 변경 | PR | 검증된 구현 커밋 |
| --- | --- | --- | --- |
| 1 | 입력 검증 | [#14](https://github.com/zionhann/open-binancian-futures/pull/14) | `aba6002` |
| 2 | 저장소 품질 기준·CI | [#15](https://github.com/zionhann/open-binancian-futures/pull/15) | `80d749e` |
| 3 | 백테스트 시간·정보 가시성 | [#16](https://github.com/zionhann/open-binancian-futures/pull/16) | `5198d17` |
| 4 | 거래소 어댑터·주문 경계 | [#17](https://github.com/zionhann/open-binancian-futures/pull/17) | `5179e3c` |
| 5 | 실거래 주문 기록·복구 | [#18](https://github.com/zionhann/open-binancian-futures/pull/18) | `b45b1e9` |

4번의 주입형 실행기 실제 실행 통합은 의존 작업인 5번에서 완료했다. 4번만 적용한 상태에 실거래 복구 보장이 있다고 해석하면 안 된다.

## AC-1–3 근거

| AC | 테스트·문서 근거 |
| --- | --- |
| 1.1 | `test_input_validation.py`: `test_invalid_leverage`, `test_settings_reject_integer_coercion` |
| 1.2 | 같은 파일의 `test_invalid_initial_balance`, `test_invalid_position_size`, `test_invalid_warmup`, `test_valid_boundaries_and_positional_order` |
| 1.3 | `test_execution_config.py`의 시간대·기존 위치 인자·주문량·증거금 회귀 테스트 |
| 1.4 | `test_cli_environment_rejects_invalid_values`, `test_cli_environment_accepts_integer_strings_and_zero_warmup` |
| 2.1 | `pyproject.toml`과 `.github/workflows/ci.yml`의 동일한 명시적 검사 명령 |
| 2.2 | Python 3.12·3.13 CI의 설치·pip check·pytest·Ruff·mypy |
| 2.3 | 패키지 전체 Ruff·mypy 통과, 타입 예외는 타입 정보가 없는 Binance 외부 모듈에 한정 |
| 2.4 | 별도 품질 PR #15; 주문·체결·복구 동작 변경은 후속 PR에 분리 |
| 3.1 | `test_backtesting_fill_policy.py`의 기본 NEXT_OPEN·명시적 CLOSE 테스트, README 전환 예제 |
| 3.2 | `test_prior_fill_cannot_be_cancelled_by_close_callback`, 기존 지연 체결·체결 훅 생성 주문 테스트 |
| 3.3 | `test_all_views_completed_and_isolated`, `test_custom_load_never_receives_future_and_raw_close_time_survives`, 생성자 워밍업·월간 봉 테스트 |
| 3.4 | `test_all_symbols_filled_before_callbacks_and_hook_orders_deferred`, 공유 잔고 회귀 테스트 |
| 3.5 | `test_stop_loss_wins_when_stop_and_take_profit_both_trigger`, README OHLC 순서 한계 설명 |
| 3.6 | `test_default_next_open_and_end_reason`, `test_last_next_open_is_cancelled_and_margin_released` |
| 3.7 | `test_strategy_failure_propagates`, `test_cli_strategy_failure_has_nonzero_exit` |
| 3.8 | 가시 데이터 마지막 인덱스 검사와 기존 2·3인자 콜백 테스트, README `load()` 전환 예제 |

`test_backtesting_causality.py`는 별도 파일명이 없는 위 인과성 테스트를 포함한다. `test_fresh_runs_are_deterministic`는 반복 실행 결과 일치를 검증한다. 체결 훅의 시장가 주문이 미래 종가로 예약 증거금을 계산하던 검토 발견 사항은 `test_fill_hook_market_reservation_uses_known_open`의 네 사례로 수정·검증했다.

## AC-4–5 근거

[어댑터 전환 문서](exchange-adapter.md)와 [실거래 운영 문서의 AC 표](live-operations.md#acceptance-mapping)를 참조한다. SDK 모델 계약, 실제 별도 프로세스 잠금·비정상 종료, 가짜 거래소를 이용한 기본·주입형 실행, 응답 유실·재시작·연결 복구를 자동 테스트한다.

독립 검토에서 발견한 네 문제는 수정 전 실패를 확인한 일곱 회귀 사례로 추가 검증했다.

- 이전 연결의 비동기 전략·체결 알림이 복구 후 오래된 판단으로 주문하는 문제: `test_pre_disconnect_async_decision_cannot_send_after_recovery`.
- 주문 접수 후 조회 실패와 레버리지 변경 통신 실패를 전략 오류로 오인하는 문제: `test_gateway_infrastructure_failure_recovers_without_strategy_latch`.
- 잘못된 봉 이벤트가 복구 위치를 앞당기는 문제: `test_malformed_closed_candle_does_not_advance_recovery_cursor`.
- 시작·복구 동기화 중 마감된 봉으로 거래하는 문제: `test_sync_completion_candles_warm_indicators_without_strategy_replay`.

## 최종 확인

구현 커밋 `b45b1e9`에서 Python 3.12로 독립 재실행한 결과:

- `python -m pytest -q`: **253 passed**.
- `python -m ruff check --config pyproject.toml open_binancian_futures`: 통과.
- `python -m mypy --config-file pyproject.toml open_binancian_futures`: 22개 소스 파일 통과.
- `python -m pip check`: 의존성 충돌 없음.

후속 문서 커밋과 PR CI의 최종 상태는 전달 시 다시 확인한다. 전체 테스트 통과가 실제 거래소 운영 검증을 의미하지는 않는다. [수동 테스트넷 체크리스트](live-operations.md#manual-testnet-checklist-not-executed-by-automated-tests)는 아직 실행하지 않았다.

## 남는 운영 경계

- 보장은 프레임워크 관리 주문 API에 적용한다. 기존 동기 보호 주문 헬퍼는 문서의 비동기 `submit_order` 예제로 전환해야 한다.
- 로컬 잠금은 같은 기록 경로에 한정한다. 실제 배포에서는 재배포 후에도 유지되는 경로를 지정해야 한다.
- 진행 중인 동기 REST 요청은 끝난 뒤 종료할 수 있다. 통신이 완전히 끊긴 동안의 체결 알림·손익 이력을 임의로 재구성하지 않는다.
- 엄격한 백테스트 정보 제한을 위해 지표를 제한된 데이터로 재계산하므로 긴 평가의 실행 비용이 커질 수 있다. 수수료·슬리피지·펀딩 모델 확장은 이번 범위 밖이다.

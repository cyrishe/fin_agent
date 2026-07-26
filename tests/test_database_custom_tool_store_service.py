from src.services.database_custom_tool_store_service import DatabaseCustomToolStoreService


def test_owner_scoped_tool_name_is_stable_and_within_storage_limit() -> None:
    first = DatabaseCustomToolStoreService._owner_scoped_name(
        "ct_abu_market_buy_decision",
        "guest-a",
    )
    same = DatabaseCustomToolStoreService._owner_scoped_name(
        "ct_abu_market_buy_decision",
        "guest-a",
    )
    other = DatabaseCustomToolStoreService._owner_scoped_name(
        "ct_abu_market_buy_decision",
        "guest-b",
    )

    assert first == same
    assert first != other
    assert first.startswith("ct_abu_market_buy_decision_")
    assert len(first) <= 64

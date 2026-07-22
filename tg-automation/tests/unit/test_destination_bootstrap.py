from tg_automation.core.config import Settings
from tg_automation.destinations.bootstrap import bootstrap_test_destinations
from tg_automation.destinations.service import DestinationService


def test_bootstrap_creates_only_configured_test_destinations(session, monkeypatch) -> None:
    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "tg_automation.destinations.bootstrap.get_session_factory",
        lambda: lambda: SessionContext(),
    )
    settings = Settings(
        _env_file=None,
        telegram_test_channel_id="-1004425376210",
        telegram_test_group_id="-5557267112",
    )

    bootstrap_test_destinations(settings)
    bootstrap_test_destinations(settings)

    rows = DestinationService(session).list(is_test=True)
    assert [(row.source_code, row.telegram_chat_id) for row in rows] == [
        ("test_channel", "-1004425376210"),
        ("test_group", "-5557267112"),
    ]

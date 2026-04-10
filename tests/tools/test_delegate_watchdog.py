from tools.delegate_tool import _watchdog_reason


def test_watchdog_reason_triggers_on_idle_child():
    activity = {"seconds_since_activity": 190, "current_tool": "read_file"}
    watch_state = {"same_tool_repeat_count": 0, "last_tool_name": None}
    reason = _watchdog_reason(activity, watch_state, idle_timeout=180, same_tool_limit=8)
    assert reason == "Delegate watchdog: child idle on read_file for 190s"


def test_watchdog_reason_triggers_on_repeat_tool_loop():
    activity = {"seconds_since_activity": 1, "current_tool": "search_files"}
    watch_state = {"same_tool_repeat_count": 8, "last_tool_name": "search_files"}
    reason = _watchdog_reason(activity, watch_state, idle_timeout=180, same_tool_limit=8)
    assert reason == "Delegate watchdog: suspected loop — search_files repeated 8 times"


def test_watchdog_reason_returns_none_when_child_is_healthy():
    activity = {"seconds_since_activity": 5, "current_tool": "read_file"}
    watch_state = {"same_tool_repeat_count": 2, "last_tool_name": "read_file"}
    reason = _watchdog_reason(activity, watch_state, idle_timeout=180, same_tool_limit=8)
    assert reason is None

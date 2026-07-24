from pathlib import Path


def test_live_search_console_contract() -> None:
    panel = Path(__file__).with_name("panel.html").read_text(encoding="utf-8")

    assert 'id="tdSearchConsole"' in panel
    assert "renderTalentSearchConsole" in panel
    assert "本轮总进度" in panel
    assert "已发现候选人" in panel
    assert "已扫描公开结果" in panel
    assert "查询进度" in panel
    assert "可用搜索引擎" in panel
    assert "现在正在做什么" in panel
    assert "接下来系统会做什么" in panel
    assert "每 15 秒报告在线" in panel
    assert "每 45 秒续租" in panel
    assert "所有公开搜索引擎暂时不可用" in panel
    assert "manage_nexus_worker_startup.ps1" in panel
    assert "-Action Status" in panel
    assert "-Action Restart" in panel
    assert "不要绕过 CAPTCHA" in panel
    assert "Review Pool" not in panel


def test_task_rows_show_structured_progress() -> None:
    panel = Path(__file__).with_name("panel.html").read_text(encoding="utf-8")

    assert "function talentTaskEffectiveCounts(task)" in panel
    assert "result.selected_contacts??result.contact_ready" in panel
    assert "result.scanned_observations??result.raw_result_count" in panel
    assert "const pc=talentTaskEffectiveCounts(t)" in panel
    assert "progress_counts" in panel
    assert "candidates_found" in panel
    assert "results_scanned" in panel
    assert "queries_completed" in panel
    assert "queries_total" in panel
    assert "transition:width .35s" in panel


if __name__ == "__main__":
    test_live_search_console_contract()
    test_task_rows_show_structured_progress()
    print("Talent live search console contract: PASSED")

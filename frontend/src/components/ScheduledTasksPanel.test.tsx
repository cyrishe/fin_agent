import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createScheduledTask, previewScheduledTask } from "../api";
import type { ScheduledTaskDraft } from "../types";
import ScheduledTasksPanel from "./ScheduledTasksPanel";

const preview: ScheduledTaskDraft = {
  requirement_brief: "每天九点查询行情",
  trigger: { cron: "0 9 * * *", timezone: "Asia/Shanghai" },
  execution_plan: {
    steps: [{
      step_id: "quote",
      type: "tool",
      target_ref: { kind: "tool", name: "stock_realtime_quote" },
      inputs: { code: "600519" },
      depends_on: [],
    }],
  },
  next_run_at: "2026-08-01T01:00:00+00:00",
};

describe("ScheduledTasksPanel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the preview-confirm workflow and task management regions", () => {
    const html = renderToStaticMarkup(<ScheduledTasksPanel />);

    expect(html).toContain('aria-label="定时任务"');
    expect(html).toContain("用自然语言创建任务");
    expect(html).toContain("<textarea");
    expect(html).toContain("生成预览");
    expect(html).toContain("我的定时任务");
    expect(html).toContain("正在加载");
  });

  it("sends natural language to preview without persisting", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      preview,
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(previewScheduledTask("每天九点查询行情")).resolves.toEqual(preview);
    expect(fetchMock).toHaveBeenCalledWith("/api/schedules/preview", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ instruction: "每天九点查询行情" }),
    }));
  });

  it("creates from the confirmed draft with an idempotency key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      schedule: {
        ...preview,
        schedule_id: "sch_1",
        enabled: true,
        revision_no: 1,
      },
    }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    await createScheduledTask({
      instruction: "每天九点查询行情",
      draft: preview,
      idempotencyKey: "request-1",
    });

    const options = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/schedules");
    expect(options.headers).toEqual(expect.objectContaining({
      "Idempotency-Key": "request-1",
    }));
    expect(JSON.parse(String(options.body))).toEqual({
      instruction: "每天九点查询行情",
      draft: preview,
    });
  });
});

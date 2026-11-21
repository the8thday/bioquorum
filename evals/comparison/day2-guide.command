#!/usr/bin/env python3
"""Guide a human through the frozen two-stage delivery on a local Mac."""

import csv
import secrets
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKET = ROOT / "evals/results/v10/day2"
PROMPTS = {
    "baseline": "请只根据第一天留下的研究记录，用中文告诉我：原来的判断是什么，关键数字是多少，有哪些适用范围或限制？请指出数字和判断各自的证据位置。这一步只回顾旧记录，完成后停下。",
    "impact": "现在收到一份更正表。请结合更正说明和原始研究记录，列出需要复核的计算、结果和报告决定，逐项指出依据。先列影响范围，到这里停下，等我确认后再重算。",
    "revision": "请用更正表重算，或核对工作台刚刚用更正表得到的结果。写明更正后的两组计数和比例、供者配对方向、原判断和报告决定该如何处理。给出可以复查的证据位置和重算步骤。用中文输出一份可交给下一位同事的简短修订记录。无法确认的地方请明确写出来。",
}
NAMES = {"bioquorum": "BioQuorum", "general-agent": "通用 Codex"}
LABELS = {"baseline": "原判断", "impact": "影响范围", "revision": "最终修订"}


def clipboard(value):
    subprocess.run(["pbcopy"], input=str(value).encode(), check=True)


def deliver_prompt(value):
    print("\n提示词已复制。到工作窗口按 ⌘V，再发送。\n\n" + value, flush=True)
    clipboard(value)


def save(rows, path):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def capture(prompt, stage, start):
    while True:
        input(
            f"\n确认「{LABELS[stage]}」后，选中要交出的答案文字，按 ⌘C。回到本向导按回车，记录答案和时间。"
        )
        elapsed = int(time.monotonic() - start)
        answer = subprocess.check_output(["pbpaste"]).decode().strip()
        if answer and answer != prompt:
            return answer, elapsed
        print("还没有复制答案。若未得出结论，可复制自己写的原因或错误信息。")


def launch(arm, folder, root):
    if arm == "bioquorum":
        # Use the checked-out workbench, regardless of the user's shell environment.
        with root.joinpath("workbench.log").open("a") as log:
            return subprocess.Popen(
                [str(ROOT / ".venv/bin/bq"), "workbench", "-C", str(folder / "project")],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    cli = shutil.which("codex") or str(Path.home() / ".local/bin/codex")
    command = root / "open-codex.command"
    command.write_text(
        "#!/bin/zsh\ncd "
        + shlex.quote(str(folder))
        + "\nexec "
        + shlex.join([cli, "-C", str(folder), "-m", "gpt-6.1-sol"])
        + "\n"
    )
    command.chmod(0o700)
    subprocess.run(["open", "-a", "Terminal", str(command)], check=True)
    return None


def session(arm, tag, order, base, experience):
    root = Path(tempfile.mkdtemp(prefix=arm + "-", dir=base))
    folder = root / "participant"
    shutil.copytree(PACKET / arm, folder)
    path = folder / "record.csv"
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_stage = {r["stage"]: r for r in rows}
    for row in rows:
        row.update(participant_code=tag, arm=arm, order=order)
    by_stage["assistance_or_prior_exposure"]["response_or_evidence"] = experience
    save(rows, path)
    print(f"\n本组使用 {NAMES[arm]}。本次结果保存在 {folder}")
    proc = None
    try:
        input("把电脑交给本组接手者。准备好打开工作窗口后，按回车。")
        proc = launch(arm, folder, root)
        input("工作窗口就绪后，请先回到向导。准备开始查看第一天材料时按回车，开始计时。")
        start = time.monotonic()
        if arm == "bioquorum":
            print(
                "浏览器打开后，点 Research lines → Circulating monocyte interpretation。先读摘要，点进第一天的成员会话，可在会话底部输入框发送提示词。"
            )
        else:
            print("在新打开的 Codex 终端中操作。如询问是否信任本次材料文件夹，核对文件夹后继续。")
        for stage, common in PROMPTS.items():
            prompt = common
            if stage == "baseline" and arm == "general-agent":
                prompt = (
                    "请只读取当前文件夹中的 day-one.md、tool-history.json、report-decision.txt 和 cells.csv。\n"
                    + common
                )
            if stage == "impact":
                dest = folder / "correction"
                shutil.copytree(PACKET / "coordinator/correction", dest)
                notice = (dest / "notice.txt").read_text().strip()
                print("\n第二阶段已解锁。更正说明：" + notice)
                if arm == "bioquorum":
                    clipboard(dest / "cells.csv")
                    print(
                        "新版 CSV 的完整路径已复制。点 Materials → 旧 cells.csv 的 Replace version → 选择文件。\n在文件选择窗口按 ⇧⌘G、⌘V、回车，选中新版 cells.csv。"
                    )
                    input("已选好新版 CSV 后，回本向导按回车，复制替换原因。")
                    clipboard(notice)
                    print(
                        "来源栏填「供应商更正表」。在 Replacement reason 按 ⌘V，点 Save replacement。在旧版上点 View impact，先查看范围。"
                    )
                    input("已看到影响范围后，回到向导按回车；下一条提示词会复制好。")
                    prompt = "更正说明：" + notice + "\n" + common
                else:
                    prompt = (
                        "新版文件在当前目录的 correction/cells.csv，更正说明在 correction/notice.txt。请保留原 cells.csv。\n"
                        + common
                    )
            if stage == "revision" and arm == "bioquorum":
                print(
                    "\n在旧版 cells.csv 的 View impact 中点 Recompute with replacement，查看新旧值，再回到研究线检查原决定。可以在修订会话里使用下一条提示词。"
                )
            if stage == "revision" and arm == "general-agent":
                prompt += " 请把修订记录保存成当前文件夹内的 revision.md。"
            deliver_prompt(prompt)
            answer, elapsed = capture(prompt, stage, start)
            by_stage[stage].update(response_or_evidence=answer, elapsed_seconds=elapsed)
            save(rows, path)
            print(f"已保存「{LABELS[stage]}」，累计 {elapsed} 秒。")
        usage = input(
            "请填写本组模型和用量（可写「未调模型」，或「GPT-6.1 Sol，实际用量未知」）："
        ).strip()
        extra = input("若另有协助、中断、失败或证据文件位置，请填写；没有就按回车：").strip()
        by_stage["model_and_usage"]["response_or_evidence"] = usage or "未登记"
        by_stage["assistance_or_prior_exposure"]["response_or_evidence"] = experience + " " + extra
        by_stage["decision_and_boundaries"]["response_or_evidence"] = "见 revision 行的接手者原文。"
        save(rows, path)
        print("\n本组已完成。可交给协调人的记录和项目位于：\n" + str(folder))
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            proc.wait()


def main():
    print(
        "0.10.8 正式接手向导（在准备好材料的这台 Mac 上使用）\n"
        "接手者应未参加 B-29 第一天研究。请让接手者自己操作和确认答案。模型可选，若调用，两组均使用 GPT-6.1 Sol。也可直接复制自己写的观察。\n"
        "两位接手者各做一组：第一位选 1，第二位重新启动选 2；只有一位做两组：选 3。\n"
        "1  BioQuorum\n2  通用 Codex\n3  同一人做两组，由向导随机确定先后"
    )
    choice = input("输入 1、2 或 3，然后按回车：").strip()
    if choice not in {"1", "2", "3"}:
        raise SystemExit("尚未开始。请重新启动并选择 1、2 或 3。")
    tag = input("输入接手者代号（例如 P01、P02）：").strip()
    if not tag:
        raise SystemExit("尚未开始。请重新启动并填一个匿名代号。")
    experience = (
        input("以前看过这道题、看过另一组材料或接受过帮助吗？请说明；没有就按回车：").strip()
        or "无"
    )
    base = Path.home() / "BioQuorum-Day2"
    base.mkdir(exist_ok=True)
    arms = list(NAMES)
    if choice == "3":
        if secrets.randbelow(2):
            arms.reverse()
    else:
        arms = [arms[int(choice) - 1]]
    for n, arm in enumerate(arms, start=1):
        session(
            arm, tag, n, base, experience if n == 1 else experience + "；刚完成另一组相同任务。"
        )
    print(
        "\n请把这句话发回原对话：两组都做完了，请在 ~/BioQuorum-Day2 核对两份正式记录。"
        if choice == "3"
        else "\n本组已保存。两位接手者都完成后，请把这句话发回原对话：两组都做完了，请在 ~/BioQuorum-Day2 核对两份正式记录。"
    )


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n向导已结束。已提交的阶段保留在本次结果文件夹。")

"""GitHub PRs using the user's Git credential helper or GH_TOKEN."""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from .core import DeckError, git


class GitHub:
    def __init__(self, project):
        self.project = project
        url = git(project.root, "remote", "get-url", project.config.get("remote", "origin")).stdout.strip()
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/(.+?)(?:\.git)?", url)
        if not match:
            raise DeckError("PR 功能目前支持 github.com 的 HTTPS/SSH remote")
        self.owner, self.repo = match.groups()

    def token(self):
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            return token
        try:
            p = subprocess.run(["git", "credential", "fill"], cwd=self.project.root,
                               input="protocol=https\nhost=github.com\n\n", capture_output=True,
                               text=True, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), timeout=15)
            for line in p.stdout.splitlines():
                if line.startswith("password="):
                    return line.split("=", 1)[1]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None

    def api(self, path, method="GET", body=None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "taskdeck/0.1"}
        token = self.token()
        if token:
            headers["Authorization"] = "Bearer " + token
        if method != "GET" and not token:
            raise DeckError("未找到 GitHub 凭证，请配置 Git credential helper 或 GH_TOKEN")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"https://api.github.com/repos/{self.owner}/{self.repo}/{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25) as res:
                return json.load(res)
        except urllib.error.HTTPError as e:
            # Never include headers or credentials in errors/logs.
            raise DeckError(f"GitHub 请求失败 HTTP {e.code}；请检查登录、权限和仓库") from e
        except (OSError, urllib.error.URLError) as e:
            raise DeckError(f"GitHub 连接失败：{e.reason if hasattr(e, 'reason') else e}") from e

    def find(self, branch):
        params = urllib.parse.urlencode({"head": f"{self.owner}:{branch}", "base": self.project.base, "state": "all", "per_page": 100})
        pulls = self.api("pulls?" + params)
        # Prefer an open PR; otherwise the newest result for this exact base/head.
        return next((p for p in pulls if p["state"] == "open"), pulls[0] if pulls else None)

    @staticmethod
    def summary(pr):
        return {"number": pr["number"], "url": pr["html_url"], "state": "merged" if pr.get("merged_at") else pr["state"],
                "merge_commit": pr.get("merge_commit_sha") if pr.get("merged_at") else None}

    def sync(self, task):
        state = task.state
        if not state.get("branch"):
            return None
        pr = self.find(state["branch"])
        if pr:
            updates = {"pr": self.summary(pr)}
            if pr.get("merged_at") and pr.get("merge_commit_sha"):
                updates["completed_commit"] = pr["merge_commit_sha"]
            task.save(**updates)
        else:
            task.save(pr=None)
        return task.state.get("pr")

    def create(self, task, title=None, body=None):
        state = task.state
        path, branch = state.get("worktree"), state.get("branch")
        if not path or not branch:
            raise DeckError("任务尚未创建 worktree")
        if state.get("status") in {"preparing", "waiting"}:
            raise DeckError("任务正在准备或等待审批，请先处理当前状态")
        if git(path, "status", "--porcelain").stdout.strip():
            raise DeckError("工作目录还有未提交修改，请先完成提交")
        if int(git(path, "rev-list", "--count", self.project.base_ref() + "..HEAD").stdout.strip()) == 0:
            raise DeckError("任务分支没有新的提交")
        existing = self.find(branch)
        if existing and existing["state"] != "open":
            raise DeckError("该分支已有关闭/合并的 PR，请核对任务交付状态")
        delivery = task.folder.relative_to(self.project.root) / "delivery.md"
        if body is None:
            file = __import__("pathlib").Path(path) / delivery
            if not file.exists():
                raise DeckError("缺少 delivery.md，请先记录实现、验证结果和未验证项")
            body = file.read_text()
        git(path, "push", "-u", self.project.config.get("remote", "origin"), branch, timeout=90)
        pr = existing or self.api("pulls", "POST", {"title": title or f"{task.id}: {task.title}", "body": body,
                                                 "head": branch, "base": self.project.base})
        task.save(pr=self.summary(pr))
        task.event("pr", url=pr["html_url"])
        return self.summary(pr)

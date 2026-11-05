"""Git and GitHub plumbing for skill-fix proposals (docs/PHASE5_CONTRACTS.md, guard rails).

A proposal lives in its own worktree data/feedback/worktrees/<pr_id> on the branch
skillfix/<pr_id>, made from origin/master. The live checkout's files are never edited, the
branch is pushed with its literal name, the PR is opened with `gh api` and never merged.

Every subprocess call takes an argument list (no shell). Anything that comes from feedback
or from the model (diff, commit message, PR body) goes through a file, never through argv.
"""
import ast
import json
import os
import re
import subprocess
from pathlib import Path

from ..pipeline import DATA, ROOT

ID_RE = re.compile(r"^pr_[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
CLUSTER_RE = re.compile(r"^[a-z_]{1,32}:[a-z0-9_]{1,64}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
BASE = "master"
SKILL = "skills/explainer-video-pipeline"
VERSION_PATH = f"{SKILL}/VERSION"
ALLOWED_EXACT = (f"{SKILL}/SKILL.md", f"{SKILL}/scripts/pronounce.json",
                 "server/app/workflows/nodes.py", "server/app/pipeline.py", "server/app/llm.py")
ALLOWED_SCRIPT = re.compile(r"^skills/explainer-video-pipeline/scripts/[A-Za-z0-9_]+\.py$")
MAX_CHANGED_LINES = 40
MAX_DIFF_BYTES = 20000
LLM_LOCKED = ("MODEL_ID", "REGION")  # llm.py constants that are not prompt strings
WORKTREES = DATA / "feedback" / "worktrees"


class GitError(RuntimeError):
    pass


class DiffRejected(ValueError):
    pass


def valid_id(pr_id) -> bool:
    return isinstance(pr_id, str) and bool(ID_RE.match(pr_id))


def branch_name(pr_id: str) -> str:
    if not valid_id(pr_id):
        raise ValueError(f"bad proposal id: {pr_id!r}")
    return f"skillfix/{pr_id}"


def _run(args: list, cwd, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run([str(a) for a in args], cwd=str(cwd), capture_output=True, text=True,
                       timeout=timeout, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if check and r.returncode != 0:
        raise GitError(f"{args[0]} {args[1] if len(args) > 1 else ''} failed "
                       f"({r.returncode}): {(r.stderr or r.stdout).strip()[-800:]}")
    return r


def git(args: list, cwd, timeout: int = 300, check: bool = True) -> str:
    return _run(["git", *args], cwd, timeout, check).stdout.strip()


# ---------------------------------------------------------------- repo

def repo_root() -> Path:
    """The serving checkout (EVP_FEEDBACK_REPO overrides it, e.g. a local clone for tests)."""
    env = os.environ.get("EVP_FEEDBACK_REPO")
    start = Path(env) if env else ROOT
    return Path(git(["rev-parse", "--show-toplevel"], start)).resolve()


def origin_repo(root=None) -> tuple:
    """(owner, repo) of origin on GitHub; ValueError when origin is not a GitHub repo."""
    url = git(["remote", "get-url", "origin"], root or repo_root())
    m = re.match(r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
                 r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", url)
    if not m:
        raise ValueError("origin 不是 GitHub 仓库，无法开 PR")
    return m.group(1), m.group(2)


def fetch(root=None) -> str:
    """git fetch origin master; returns the sha of origin/master."""
    root = root or repo_root()
    git(["fetch", "--quiet", "origin", BASE], root, timeout=180)
    return git(["rev-parse", f"origin/{BASE}"], root)


def show(path: str, rev: str, root=None):
    """File content at rev, None when the file does not exist there."""
    r = _run(["git", "show", f"{rev}:{path}"], root or repo_root(), check=False)
    return r.stdout if r.returncode == 0 else None


def head(root=None) -> str:
    return git(["rev-parse", "HEAD"], root or repo_root())


def contains(sha: str, root=None) -> bool:
    """Whether HEAD of the checkout already contains commit sha (no fetch, no pull)."""
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        return False
    r = _run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], root or repo_root(),
             check=False)
    return r.returncode == 0


# ---------------------------------------------------------------- diff checks

def allowed_path(p: str) -> bool:
    return p in ALLOWED_EXACT or bool(ALLOWED_SCRIPT.match(p))


def diff_paths(diff: str) -> list:
    """Paths a unified diff touches. Rejects renames, mode changes, new/deleted and binary
    files, and paths that are absolute or climb out of the repo."""
    paths = []
    for line in diff.splitlines():
        if line.startswith(("rename ", "copy ", "new file mode", "deleted file mode",
                            "old mode", "new mode", "similarity index", "Binary files",
                            "GIT binary patch")):
            raise DiffRejected(f"不允许的改动类型：{line[:60]}")
        if line.startswith(("--- ", "+++ ")):
            p = line[4:].split("\t")[0].strip()
            if p == "/dev/null":
                raise DiffRejected("不允许新建或删除文件")
            if not re.match(r"^[ab]/", p):
                raise DiffRejected(f"diff 路径必须是 a/… b/… 形式：{p[:80]}")
            p = p[2:]
            if p.startswith("/") or "\\" in p or ".." in p.split("/") or not p:
                raise DiffRejected(f"非法路径：{p[:80]}")
            paths.append(p)
    if not paths:
        raise DiffRejected("diff 里没有文件")
    return list(dict.fromkeys(paths))


def changed_lines(diff: str) -> int:
    return sum(1 for ln in diff.splitlines()
               if ln[:1] in "+-" and not ln.startswith(("+++ ", "--- ")))


def validate_diff(diff: str) -> list:
    """Path and size checks, done before any git command; returns the paths."""
    if not isinstance(diff, str) or not diff.strip():
        raise DiffRejected("diff 为空")
    if len(diff.encode()) > MAX_DIFF_BYTES:
        raise DiffRejected(f"diff 超过 {MAX_DIFF_BYTES} 字节")
    paths = diff_paths(diff)
    bad = [p for p in paths if not allowed_path(p)]
    if bad:
        raise DiffRejected(f"不允许修改的文件：{', '.join(bad)}")
    n = changed_lines(diff)
    if n == 0:
        raise DiffRejected("diff 没有改动行")
    if n > MAX_CHANGED_LINES:
        raise DiffRejected(f"diff 改动 {n} 行，超过上限 {MAX_CHANGED_LINES}")
    return paths


class _Mask(ast.NodeTransformer):
    """Replaces the editable values of a file with a placeholder and records them."""

    def __init__(self, path: str):
        self.path, self.values = path, []

    def _mask(self, node):
        self.values.append(node)
        return ast.copy_location(ast.Constant("<editable>"), node)

    def visit_Module(self, node):
        for st in node.body:
            if isinstance(st, ast.Assign) and all(
                    isinstance(t, ast.Name) and re.match(r"^[A-Z][A-Z0-9_]*$", t.id)
                    for t in st.targets):
                names = [t.id for t in st.targets]
                if self.path == "server/app/llm.py":
                    if isinstance(st.value, ast.Constant) and isinstance(st.value.value, str) \
                            and not set(names) & set(LLM_LOCKED):
                        st.value = self._mask(st.value)
                elif self.path != "server/app/workflows/nodes.py":
                    st.value = self._mask(st.value)
        self.generic_visit(node)
        return node

    def visit_Dict(self, node):
        if self.path == "server/app/workflows/nodes.py":
            node.values = [self._mask(v) if isinstance(k, ast.Constant) and k.value == "default"
                           else v for k, v in zip(node.keys, node.values)]
        self.generic_visit(node)
        return node

    def visit_Call(self, node):
        if ALLOWED_SCRIPT.match(self.path) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_argument":
            for kw in node.keywords:
                if kw.arg == "default":
                    kw.value = self._mask(kw.value)
        self.generic_visit(node)
        return node


def _literal(node) -> bool:
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return False


def check_semantics(path: str, old, new: str) -> None:
    """The edit stays inside what the contract allows for that file:
    pipeline.py UPPER_CASE constants, nodes.py param defaults, llm.py prompt strings,
    scripts/*.py argparse defaults and UPPER_CASE constants (new values must be literals),
    pronounce.json still valid JSON; SKILL.md is free text."""
    if old is None:
        raise DiffRejected(f"{path} 在 master 上不存在")
    if path.endswith(".json"):
        try:
            obj = json.loads(new)
        except ValueError as e:
            raise DiffRejected(f"{path} 不是合法 JSON：{e}")
        if not isinstance(obj, dict):
            raise DiffRejected(f"{path} 顶层必须是对象")
        return
    if not path.endswith(".py"):
        return
    try:
        t_old, t_new = ast.parse(old), ast.parse(new)
    except SyntaxError as e:
        raise DiffRejected(f"{path} 修改后有语法错误：{e}")
    m_old, m_new = _Mask(path), _Mask(path)
    d_old, d_new = ast.dump(m_old.visit(t_old)), ast.dump(m_new.visit(t_new))
    if d_old != d_new:
        raise DiffRejected(f"{path} 的改动超出允许范围（只能改参数/默认值/常量/提示词）")
    for a, b in zip(m_old.values, m_new.values):
        if ast.dump(a) != ast.dump(b) and not (_literal(a) and _literal(b)):
            raise DiffRejected(f"{path} 只能改字面量的默认值/常量")


# ---------------------------------------------------------------- worktree

def worktree_path(pr_id: str, suffix: str = "") -> Path:
    branch_name(pr_id)  # validates
    return WORKTREES / (pr_id + suffix)


def create_worktree(pr_id: str, base_sha: str, root=None) -> Path:
    root = root or repo_root()
    if not SHA_RE.match(base_sha or ""):
        raise ValueError("bad base sha")
    wt = worktree_path(pr_id)
    WORKTREES.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--quiet", "-b", branch_name(pr_id), wt, base_sha], root)
    return wt


def create_base_tree(pr_id: str, base_sha: str, root=None) -> Path:
    """Detached checkout of the proposal's base (the 'before' code)."""
    root = root or repo_root()
    if not SHA_RE.match(base_sha or ""):
        raise ValueError("bad base sha")
    wt = worktree_path(pr_id, ".base")
    if (wt / ".git").exists():
        return wt
    WORKTREES.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--quiet", "--detach", wt, base_sha], root)
    return wt


def remove_worktrees(pr_id: str, root=None) -> list:
    root = root or repo_root()
    gone = []
    for wt in (worktree_path(pr_id), worktree_path(pr_id, ".base")):
        if wt.exists():
            _run(["git", "worktree", "remove", "--force", wt], root, check=False)
            gone.append(str(wt))
    _run(["git", "worktree", "prune"], root, check=False)
    return gone


def apply_diff(wt: Path, diff: str, patch_file: Path) -> list:
    """validate -> write the patch file -> git apply --check -> git apply -> semantic check."""
    paths = validate_diff(diff)
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    patch_file.write_text(diff if diff.endswith("\n") else diff + "\n")
    r = _run(["git", "apply", "--check", "--whitespace=nowarn", patch_file], wt, check=False)
    if r.returncode != 0:
        raise DiffRejected(f"git apply --check 失败：{r.stderr.strip()[-500:]}")
    git(["apply", "--whitespace=nowarn", patch_file], wt)
    for p in paths:
        check_semantics(p, show(p, "HEAD", wt), (wt / p).read_text())
    return paths


def bump_version(wt: Path) -> tuple:
    """Patch-bump skills/explainer-video-pipeline/VERSION (1.0.0 when missing)."""
    p = wt / VERSION_PATH
    old = p.read_text().strip() if p.exists() else "1.0.0"
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", old)
    if not m:
        raise ValueError(f"VERSION is not semver: {old!r}")
    new = f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"
    p.write_text(new + "\n")
    return old, new


def commit(wt: Path, paths: list, cluster: str, pr_id: str, msg_file: Path,
           reason: str = "") -> str:
    """One commit with the diff and the VERSION bump; message via -F (never argv)."""
    if not CLUSTER_RE.match(cluster or ""):
        raise ValueError(f"bad cluster: {cluster!r}")
    old, new = bump_version(wt)
    msg_file.parent.mkdir(parents=True, exist_ok=True)
    msg_file.write_text(f"skillfix({cluster}): {pr_id}, skill {old} -> {new}\n\n"
                        f"{reason.strip()[:2000]}\n\nCluster: {cluster}\nProposal: {pr_id}\n")
    git(["add", "--", *paths, VERSION_PATH], wt)
    git(["commit", "--quiet", "--no-verify", "-F", msg_file], wt)
    return git(["rev-parse", "HEAD"], wt)


def diff_stat(wt: Path, base_sha: str) -> str:
    return git(["diff", "--stat", f"{base_sha}..HEAD"], wt)


def push(pr_id: str, wt=None) -> str:
    """git push origin skillfix/<pr_id> (literal branch name from a validated id)."""
    br = branch_name(pr_id)
    git(["push", "--quiet", "origin", f"{br}:refs/heads/{br}"], wt or worktree_path(pr_id),
        timeout=180)
    return br


# ---------------------------------------------------------------- GitHub

def _gh(args: list, root=None) -> dict:
    r = _run(["gh", "api", *args], root or repo_root(), timeout=120)
    return json.loads(r.stdout or "{}")


def open_pr(pr_id: str, title: str, body_file: Path, root=None) -> dict:
    """POST repos/<owner>/<repo>/pulls from a JSON file; returns {number, url}. Never merges."""
    owner, repo = origin_repo(root)
    req = body_file.with_suffix(".request.json")
    req.write_text(json.dumps({"title": title[:200], "head": branch_name(pr_id), "base": BASE,
                               "body": body_file.read_text(), "draft": False},
                              ensure_ascii=False))
    out = _gh(["-X", "POST", f"repos/{owner}/{repo}/pulls", "--input", req], root)
    return {"number": int(out["number"]), "url": str(out["html_url"])}


def pr_state(number: int, root=None) -> dict:
    """{state: open|closed|merged, merge_commit: sha|None, url}."""
    owner, repo = origin_repo(root)
    out = _gh([f"repos/{owner}/{repo}/pulls/{int(number)}"], root)
    merged = bool(out.get("merged") or out.get("merged_at"))
    state = "merged" if merged else ("closed" if out.get("state") == "closed" else "open")
    sha = out.get("merge_commit_sha") if merged else None
    return {"state": state, "merge_commit": sha if isinstance(sha, str) and SHA_RE.match(sha)
            else None, "url": out.get("html_url")}

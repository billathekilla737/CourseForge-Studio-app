"""
test_courseforge_assistant.py - the Assistant's gate and plumbing.

Dependency-free (stdlib unittest). Covers:
  - the permission classifier: reads and dry runs pass, Canvas writes ask
  - the hook process end to end: no app -> deny; app says allow -> allow;
    app says deny -> deny with the person's reason
  - the token file Python writes is the one the skill's PowerShell reads
  - stream-json parsing into UI events
  - the settings and system-prompt files a session is launched with

    python skill\\scripts\\test_courseforge_assistant.py
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cf_assistant_hook as gate      # noqa: E402
import courseforge_assistant as A     # noqa: E402

CWD = r"C:\Users\someone\Documents\CourseForge\734975"


def bash(cmd, **kw):
    ti = {"command": cmd}
    ti.update(kw)
    return gate.classify("Bash", ti, CWD)


class ClassifierTests(unittest.TestCase):
    def test_reads_and_dry_runs_allow(self):
        for cmd in (
            'powershell -NoProfile -File "C:\\s\\Dump-CanvasContent.ps1" -WorkDir .\\work',
            'python "C:\\s\\restyle_html.py" transform .\\work --look clean',
            'python restyle_html.py verify .\\work',
            'powershell -File Push-CanvasRemediation.ps1 -WorkDir .\\work',      # dry run
            'powershell -File Push-CanvasPages.ps1 -ManifestPath m.json -WhatIf',
            'powershell -File Trim-CanvasNav.ps1 -WhatIf',
            'powershell -File Export-CanvasCourse.ps1 -OutDir .\\backup',
            'Get-ChildItem .\\work | Select-Object Name',
            'curl -s -H "Authorization: Bearer x" https://a/api/v1/courses/1/pages',
            'Invoke-RestMethod -Uri https://a/api/v1/courses/1 -Headers $h',
            'git status',
            'ls -la',
        ):
            v = bash(cmd)
            self.assertEqual(v["decision"], "allow", cmd + " -> " + v["why"])

    def test_canvas_writes_ask(self):
        for cmd in (
            'powershell -File Push-CanvasRemediation.ps1 -WorkDir .\\work -Apply',
            'powershell -File Push-CanvasRemediation.ps1 -Apply:$true -WorkDir w',
            'powershell -File Set-DueDates.ps1 -Plan p.json -Apply',
            'powershell -File Push-CanvasPages.ps1 -ManifestPath m.json',
            'powershell -File Push-CanvasProject.ps1 -Root .',
            'powershell -File Trim-CanvasNav.ps1',
            'python remediate_pptx.py deck.pptx --apply',
            'Invoke-RestMethod -Uri https://a/api/v1/courses/1/pages/x -Method Put -Headers $h -Body $b',
            'Invoke-WebRequest -Method DELETE -Uri https://a/api/v1/x',
            'irm -Uri x -Method:Post',
            'curl -X DELETE https://a/api/v1/courses/1/pages/x',
            'curl --request PUT https://a/api/v1/courses/1',
            'curl -d "wiki_page[body]=x" https://a/api/v1/courses/1/pages/x',
            'python -c "import requests; requests.post(u, data=d)"',
            "python -c \"urllib.request.Request(u, method='PUT')\"",
        ):
            v = bash(cmd)
            self.assertEqual(v["decision"], "ask", cmd + " -> " + v["why"])
            self.assertEqual(v["kind"], "canvas-write", cmd)

    def test_skill_helper_writes_ask_regression(self):
        """Live 2026-09-04: Claude created a page through the skill's own
        Invoke-CanvasApi -Method POST and the cmdlet-anchored rule let it run."""
        live = ('. "$HOME\\.claude\\skills\\courseforge\\scripts\\CanvasContext.ps1"; '
                '$ctx = Resolve-CanvasContext -CourseId 721874; $h = Get-CanvasHeaders -Context $ctx; '
                "$bytes = [Text.Encoding]::UTF8.GetBytes(($pairs -join '&')); "
                '$r = Invoke-CanvasApi -Method POST -Uri ("{0}/api/v1/courses/721874/pages" -f '
                "$ctx.Config.base_url) -Headers $h -Body $bytes -ContentType 'application/x-www-form-urlencoded'")
        v = bash(live)
        self.assertEqual(v["decision"], "ask", v["why"])
        self.assertEqual(v["kind"], "canvas-write")
        for cmd in (
            'Invoke-CanvasApi -Method Put -Uri $u -Headers $h -Body $b',
            '$p = @{ Method = "DELETE"; Uri = $u; Headers = $h }; Invoke-RestMethod @p',
            "$params = @{Uri=$u; Method='post'}; Invoke-CanvasApi @params",
            '$c = New-Object Net.Http.HttpClient; $c.PostAsync($u, $content).Result',
            '$wc = New-Object Net.WebClient; $wc.UploadString($u, "PUT", $body)',
            '$req = [Net.WebRequest]::Create($u); $req.Method = "PUT"',
            "python -c \"import http.client as h; c=h.HTTPSConnection(x); c.request('DELETE', p)\"",
            'Some-Wrapper -Uri https://mgccc.instructure.com/api/v1/courses/1/pages -Verb DELETE',
            '. .\\CanvasContext.ps1; Write-CanvasBody -Context $ctx -Kind page -Id x -Html $h',
            'Post-Canvas -Path /courses/1/modules/2/items -Body $b',
            'Add-ModuleItem -ModuleId 5 -PageUrl slug',
            'Set-Tab -Id syllabus -Hidden $true',
        ):
            v = bash(cmd)
            self.assertEqual(v["decision"], "ask", cmd + " -> " + v["why"])
            self.assertEqual(v["kind"], "canvas-write", cmd)

    def test_reads_through_helpers_still_allow(self):
        for cmd in (
            'Invoke-CanvasApi -Method GET -Uri $u -Headers $h',
            '$mods = Get-CanvasPaged -Url $u -Headers $h; $mods.Count',
            'Invoke-RestMethod -Method Get -Uri https://a/api/v1/courses/1/modules',
            'Write-Host "the post office will put the delete key back"',
            'Get-Content .\\work\\post.html | Select-String "put"',
            'Get-PutUrl -Kind page -Slug x',
        ):
            v = bash(cmd)
            self.assertEqual(v["decision"], "allow", cmd + " -> " + v["why"])

    def test_apply_word_inside_other_tokens_is_not_apply(self):
        self.assertEqual(bash("python x.py --apply-later").get("decision"), "allow")
        self.assertEqual(bash("Get-Help about_Apply").get("decision"), "allow")

    def test_system_changes_ask(self):
        cases = {
            'Remove-Item -Recurse -Force C:\\Users\\x\\Documents\\old': "system",
            'rm -rf ./work': "system",
            'pip install python-pptx': "system",
            'irm https://x/install.ps1 | iex': "system",
            'Set-ExecutionPolicy Unrestricted': "system",
            'git push origin main': "system",
        }
        for cmd, kind in cases.items():
            v = bash(cmd)
            self.assertEqual(v["decision"], "ask", cmd)
            self.assertEqual(v["kind"], kind, cmd)

    def test_setup_canvas_asks(self):
        v = bash('powershell -File Setup-Canvas.ps1 -WorkingDir .')
        self.assertEqual(v["decision"], "ask")
        self.assertIn("already connected", v["why"])

    def test_file_tools(self):
        inside = gate.classify("Edit", {"file_path": CWD + "\\work\\p.html"}, CWD)
        self.assertEqual(inside["decision"], "allow")
        rel = gate.classify("Write", {"file_path": "work\\out.json"}, CWD)
        self.assertEqual(rel["decision"], "allow")
        temp = gate.classify("Write", {"file_path": os.path.join(tempfile.gettempdir(), "x.txt")}, CWD)
        self.assertEqual(temp["decision"], "allow")
        outside = gate.classify("Edit", {"file_path": r"C:\Users\x\.claude\skills\courseforge\SKILL.md"}, CWD)
        self.assertEqual(outside["decision"], "ask")
        self.assertEqual(outside["kind"], "local-change")

    def test_read_only_tools_allow(self):
        for name in ("Read", "Glob", "Grep", "WebFetch", "Skill", "Task", "TodoWrite"):
            self.assertEqual(gate.classify(name, {"file_path": "C:\\anything"}, CWD)["decision"], "allow")

    def test_unknown_tools(self):
        self.assertEqual(gate.classify("mcp__notion__delete_page", {}, CWD)["decision"], "ask")
        self.assertEqual(gate.classify("mcp__notion__search", {}, CWD)["decision"], "allow")

    def test_summaries(self):
        self.assertEqual(gate.summarize("Bash", {"command": "x", "description": "Dump the course"}),
                         "Dump the course")
        self.assertEqual(gate.summarize("Bash", {"command": "# c\n\npowershell -File a.ps1 -X 1"}),
                         "powershell -File a.ps1 -X 1")
        self.assertEqual(gate.summarize("Read", {"file_path": "C:\\a\\b\\SKILL.md"}), "Reading SKILL.md")
        self.assertEqual(gate.summarize("WebFetch", {"url": "https://mgccc.instructure.com/x"}),
                         "Fetching mgccc.instructure.com")
        self.assertEqual(gate.summarize("Skill", {"skill": "courseforge"}), "Using the courseforge skill")
        long = gate.summarize("Bash", {"command": "a" * 200})
        self.assertLessEqual(len(long), 90)


class HookProcessTests(unittest.TestCase):
    """Run the real hook the way Claude Code does: JSON on stdin."""

    HOOK = os.path.join(HERE, "cf_assistant_hook.py")

    def run_hook(self, tool, tool_input, env_extra):
        env = dict(os.environ)
        env.pop("CF_ASSISTANT_PORT", None)
        env.pop("CF_ASSISTANT_SECRET", None)
        env.update(env_extra)
        req = {"tool_name": tool, "tool_input": tool_input, "cwd": CWD,
               "session_id": "s", "tool_use_id": "t", "hook_event_name": "PreToolUse"}
        cp = subprocess.run([sys.executable, self.HOOK], input=json.dumps(req).encode("utf-8"),
                            capture_output=True, timeout=60, env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr.decode("utf-8", "replace"))
        out = json.loads(cp.stdout.decode("utf-8"))["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        return out

    def test_allow_without_app(self):
        out = self.run_hook("Bash", {"command": "Get-ChildItem"}, {})
        self.assertEqual(out["permissionDecision"], "allow")

    def test_write_without_app_is_denied(self):
        out = self.run_hook("Bash", {"command": "Push-CanvasPages.ps1"}, {})
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("not running", out["permissionDecisionReason"])

    def _with_server(self, decision, reason, tool_input):
        got = {}

        def on_req(req, answer):
            got.update(req)
            answer(decision, reason)

        srv = A.PermissionServer(on_req)
        srv.start()
        try:
            out = self.run_hook("Bash", tool_input,
                                {"CF_ASSISTANT_PORT": str(srv.port),
                                 "CF_ASSISTANT_SECRET": srv.secret})
        finally:
            srv.close()
        return out, got

    def test_app_allows(self):
        out, got = self._with_server("allow", "", {"command": "Push-CanvasRemediation.ps1 -Apply"})
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertEqual(got["kind"], "canvas-write")
        self.assertEqual(got["tool_name"], "Bash")

    def test_app_denies_with_reason(self):
        out, _ = self._with_server("deny", gate.DENY_TEXT, {"command": "Trim-CanvasNav.ps1"})
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("clicked Deny", out["permissionDecisionReason"])

    def test_wrong_secret_is_denied(self):
        srv = A.PermissionServer(lambda req, answer: answer("allow", ""))
        srv.start()
        try:
            out = self.run_hook("Bash", {"command": "Trim-CanvasNav.ps1"},
                                {"CF_ASSISTANT_PORT": str(srv.port),
                                 "CF_ASSISTANT_SECRET": "wrong"})
        finally:
            srv.close()
        self.assertEqual(out["permissionDecision"], "deny")


class TokenFormatTests(unittest.TestCase):
    TOKEN = "1234~" + "Ab9" * 20

    def test_python_round_trip(self):
        self.assertEqual(A.unprotect_token_ps(A.protect_token_ps(self.TOKEN)), self.TOKEN)

    @unittest.skipIf(not shutil.which("powershell"), "no PowerShell")
    def test_powershell_reads_what_python_wrote(self):
        d = tempfile.mkdtemp(prefix="cf-tok-")
        try:
            with open(os.path.join(d, "canvas.token.enc"), "w", encoding="ascii", newline="") as f:
                f.write(A.protect_token_ps(self.TOKEN))
            ps1 = os.path.join(HERE, "CanvasToken.ps1")
            script = ("$ErrorActionPreference='Stop'; . '%s'; "
                      "(Get-CanvasToken -Dir '%s').Token" % (ps1, d))
            cp = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                 "-Command", script], capture_output=True, text=True, timeout=90)
            self.assertEqual(cp.stdout.strip(), self.TOKEN, cp.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class StreamParsingTests(unittest.TestCase):
    def make(self):
        q = queue.Queue()
        course = {"course_id": "1", "course_name": "T", "base_url": "https://x",
                  "dir": tempfile.mkdtemp(prefix="cf-sess-")}
        s = A.ClaudeSession("claude", course, "skill", 1, "s", q)
        return s, q

    def drain(self, q):
        out = []
        while not q.empty():
            out.append(q.get())
        return out

    def test_text_streams_then_tool_then_result(self):
        s, q = self.make()
        s.handle_line(json.dumps({"type": "system", "subtype": "init", "session_id": "x"}))
        s.handle_line(json.dumps({"type": "stream_event", "event": {"type": "message_start"}}))
        s.handle_line(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}}}))
        s.handle_line(json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}}}))
        s.handle_line(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Hello"}]}}))
        s.handle_line(json.dumps({"type": "stream_event", "event": {"type": "content_block_stop"}}))
        s.handle_line(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "x", "description": "Dump the course"}}]}}))
        s.handle_line(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True, "content": "boom happened"}]}}))
        s.handle_line(json.dumps({"type": "result", "is_error": False, "result": "ok"}))
        kinds = [e[0] for e in self.drain(q)]
        self.assertEqual(kinds, ["init", "text", "text", "text_end", "tool", "tool_error", "result"])

    def test_whole_text_when_no_partials(self):
        s, q = self.make()
        s.handle_line(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Whole"}]}}))
        ev = self.drain(q)
        self.assertEqual(ev[0], ("text", "Whole"))
        self.assertEqual(ev[1], ("text_end",))

    def test_subagent_chatter_is_hidden_and_second_init_ignored(self):
        s, q = self.make()
        s.handle_line(json.dumps({"type": "system", "subtype": "init"}))
        s.handle_line(json.dumps({"type": "system", "subtype": "init"}))
        s.handle_line(json.dumps({"type": "assistant", "parent_tool_use_id": "p",
                                  "message": {"content": [{"type": "text", "text": "sub"}]}}))
        s.handle_line("not json at all")
        self.assertEqual([e[0] for e in self.drain(q)], ["init"])

    def test_rate_limit_notice(self):
        s, q = self.make()
        s.handle_line(json.dumps({"type": "rate_limit_event",
                                  "rate_limit_info": {"status": "allowed"}}))
        self.assertEqual(self.drain(q), [])
        s.handle_line(json.dumps({"type": "rate_limit_event",
                                  "rate_limit_info": {"status": "rejected"}}))
        self.assertEqual(self.drain(q)[0][0], "notice")


class LaunchFilesTests(unittest.TestCase):
    def test_settings_and_prompt_and_command(self):
        d = tempfile.mkdtemp(prefix="cf-launch-")
        course = {"course_id": "734975", "course_name": "IMT 2114", "base_url": "https://x.instructure.com", "dir": d}
        try:
            sp = A.write_settings(d)
            with open(sp, encoding="utf-8") as f:
                st = json.load(f)
            hook = st["hooks"]["PreToolUse"][0]
            self.assertEqual(hook["matcher"], "")
            self.assertIn("cf_assistant_hook.py", hook["hooks"][0]["command"])
            self.assertGreaterEqual(hook["hooks"][0]["timeout"], 600)
            pp = A.write_system_prompt(course, r"C:\skill")
            with open(pp, encoding="utf-8") as f:
                text = f.read()
            for must in ("734975", "IMT 2114", "Never run Setup-Canvas", "Allow / Deny",
                         "courseforge skill", "student"):
                self.assertIn(must, text)
            s = A.ClaudeSession("claude.exe", course, r"C:\skill", 5555, "sec", queue.Queue(),
                                session_id="11111111-1111-1111-1111-111111111111")
            cmd = s.command()
            for flag in ("-p", "--input-format", "--output-format", "--settings",
                         "--append-system-prompt-file", "--session-id", "--permission-mode"):
                self.assertIn(flag, cmd)
            self.assertNotIn("--resume", cmd)
            s2 = A.ClaudeSession("claude.exe", course, r"C:\skill", 5555, "sec", queue.Queue(),
                                 session_id="11111111-1111-1111-1111-111111111111", resume=True)
            self.assertIn("--resume", s2.command())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_course_url_parsing_and_session_state(self):
        self.assertEqual(A.parse_course_url("https://mgccc.instructure.com/courses/734975/modules"),
                         ("https://mgccc.instructure.com", "734975"))
        self.assertEqual(A.parse_course_url("nonsense"), (None, None))
        d = tempfile.mkdtemp(prefix="cf-state-")
        try:
            self.assertIsNone(A.load_session_state(d))
            A.save_session_state(d, "11111111-1111-1111-1111-111111111111")
            self.assertEqual(A.load_session_state(d)["session_id"], "11111111-1111-1111-1111-111111111111")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_skill_install_on_a_fresh_pc(self):
        """Frozen behaviour, simulated: install when absent, no-op when equal,
        refresh when the bundle changes, never touch a designer's own copy."""
        root = tempfile.mkdtemp(prefix="cf-skill-")
        base = os.path.join(root, "app")
        bundled = os.path.join(base, "skill")
        home = os.path.join(root, "home")
        os.makedirs(os.path.join(bundled, "scripts"))
        os.makedirs(home)
        with open(os.path.join(bundled, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("# skill v1\n")
        with open(os.path.join(bundled, "scripts", "x.ps1"), "w", encoding="utf-8") as f:
            f.write("Write-Host hi\n")
        os.makedirs(os.path.join(bundled, "scripts", "__pycache__"))
        with open(os.path.join(bundled, "canvas.token.enc"), "w") as f:
            f.write("must-not-ship")
        target = os.path.join(home, ".claude", "skills", "courseforge")
        marker = os.path.join(target, ".installed-by-courseforge-assistant.json")
        saved = (getattr(sys, "frozen", None), A.T.app_base_dir, os.path.expanduser)
        notes = []
        try:
            sys.frozen = True
            A.T.app_base_dir = lambda: base
            os.path.expanduser = lambda p: home if p == "~" else p
            # 1. fresh PC: installed, credentials and caches left out
            self.assertEqual(A.ensure_skill_installed(notes.append), target)
            self.assertTrue(os.path.isfile(marker))
            self.assertTrue(os.path.isfile(os.path.join(target, "scripts", "x.ps1")))
            self.assertFalse(os.path.exists(os.path.join(target, "canvas.token.enc")))
            self.assertFalse(os.path.exists(os.path.join(target, "scripts", "__pycache__")))
            self.assertTrue(any("Installed" in n for n in notes))
            # 2. same bundle again: nothing happens
            notes.clear()
            A.ensure_skill_installed(notes.append)
            self.assertEqual(notes, [])
            # 3. upgraded bundle: refreshed
            with open(os.path.join(bundled, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("# skill v2\n")
            A.ensure_skill_installed(notes.append)
            with open(os.path.join(target, "SKILL.md"), encoding="utf-8") as f:
                self.assertIn("v2", f.read())
            # 4. a designer's own live skill (no marker): used as-is, untouched
            os.remove(marker)
            with open(os.path.join(target, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("# my edits\n")
            notes.clear()
            self.assertEqual(A.ensure_skill_installed(notes.append), target)
            with open(os.path.join(target, "SKILL.md"), encoding="utf-8") as f:
                self.assertIn("my edits", f.read())
            self.assertTrue(any("managed by you" in n for n in notes))
        finally:
            if saved[0] is None:
                del sys.frozen
            else:
                sys.frozen = saved[0]
            A.T.app_base_dir = saved[1]
            os.path.expanduser = saved[2]
            shutil.rmtree(root, ignore_errors=True)

    def test_child_env_strips_nested_markers(self):
        os.environ["CLAUDECODE"] = "1"
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        env = A._child_env({"CF_ASSISTANT_PORT": "1"})
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", env)
        self.assertEqual(env["CF_ASSISTANT_PORT"], "1")


if __name__ == "__main__":
    unittest.main(verbosity=1)

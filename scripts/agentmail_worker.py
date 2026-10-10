"""Host-side Agent Mail worker (Windows/macOS/Linux, Python 3.10+, standard library only).

* Runs the official agently-cli, whose OAuth token stays in the OS keychain of the user who ran
  `agently-cli auth login`. Nothing is copied into the project, SQLite or Docker.
* Never opens SQLite. It claims queued notifications from the monitor's local admin API
  (http://127.0.0.1:8080) with the random token in data/agentmail-worker.token, sends them with
  `agently-cli message +send --confirmed`, and reports only the exit code / ok / queued flags back.
  CLI stdout/stderr are never forwarded or logged (they may contain account metadata).
* Only sites whose admin setting is "Agent Mail + 启用推送" produce jobs, plus test mails an admin
  explicitly requested with the “发送测试邮件” button.

* Also runs the admin page's 「重新授权 / 登录」/「退出登录」 requests: it starts the official
  `agently-cli auth login` device flow, reports only the authorization link and user code back
  to the admin page, and re-checks the account once the user finishes in the browser. It keeps
  running while unauthorized precisely so that login can be started from the UI.
* agently-cli keeps a separate login per detected AI-agent environment (CURSOR_AGENT, CODEX_*,
  CLAUDE_CODE, WORKBUDDY_* ...). The worker strips those variables for every CLI call so the
  login made from the admin page is always the same plain-user login the worker sends with.

Usage:
  python scripts/agentmail_worker.py            # run continuously
  python scripts/agentmail_worker.py --status   # report CLI install/auth status to the admin UI, send nothing
  python scripts/agentmail_worker.py --once     # process the queue once (MAY SEND queued mail)
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI_TIMEOUT = 60
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)  # no console flashes under pythonw.exe


def cli_command():
    """Locate the native agently-cli executable (never a .cmd/.bat shim: no shell parsing of subjects)."""
    override = os.getenv('AGENTMAIL_CLI_PATH', '')
    if override:
        path = Path(override)
        if not path.is_file() or path.suffix.lower() in ('.cmd', '.bat', '.ps1'):
            raise FileNotFoundError('AGENTMAIL_CLI_PATH 请填写 CLI 原生可执行文件绝对路径')
        return [str(path.resolve())]
    if os.name == 'nt':
        roots = [Path(os.getenv('APPDATA', '')) / 'npm' / 'node_modules']
        npm = shutil.which('npm.cmd') or shutil.which('npm')
        if npm:
            try:
                out = subprocess.run([npm, 'root', '-g'], capture_output=True, text=True, timeout=30,
                                     stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout.strip()
                if out: roots.append(Path(out))
            except (OSError, subprocess.SubprocessError): pass
        for base in roots:
            files = sorted(base.glob('@tencent-qqmail/agently-cli/node_modules/@tencent-qqmail/agently-cli-win32-*/bin/agently-cli.exe'))
            if files: return [str(files[0])]
    path = shutil.which('agently-cli')
    if path and Path(path).suffix.lower() not in ('.cmd', '.bat', '.ps1'): return [path]
    raise FileNotFoundError('未找到 agently-cli，请先运行 npm install -g @tencent-qqmail/agently-cli')


AGENT_ENV = re.compile(r'^(CURSOR|CODEX|CLAUDE_CODE|CLAUDECODE|WORKBUDDY)', re.I)


def cli_env():
    env = {k: v for k, v in os.environ.items() if not AGENT_ENV.match(k)}
    env['AGENTLY_CLI_NO_UPDATE_NOTIFIER'] = '1'
    return env


def invoke(command, args, cwd=None, timeout=CLI_TIMEOUT):
    env = cli_env()
    return subprocess.run(command + args, cwd=cwd, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=timeout, env=env, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)


def parse_json(text):
    try: return json.loads(text or '')
    except ValueError:
        match = re.search(r'\{.*\}', text or '', re.S)
        try: return json.loads(match.group(0)) if match else {}
        except ValueError: return {}


def find(value, key):
    if isinstance(value, dict):
        if key in value: return value[key]
        for item in value.values():
            found = find(item, key)
            if found is not None: return found
    if isinstance(value, list):
        for item in value:
            found = find(item, key)
            if found is not None: return found
    return None


def primary_account(me):
    aliases = find(me, 'aliases') or []
    for alias in aliases:
        if isinstance(alias, dict) and alias.get('is_primary') and alias.get('email'): return alias['email']
    for alias in aliases:
        if isinstance(alias, dict) and alias.get('email'): return alias['email']
    email = find(me, 'email')
    return email if isinstance(email, str) else ''


def probe(runner=invoke, locate=cli_command):
    """CLI install + authorization status. Read-only: version, auth status, +me."""
    report = {'installed': False, 'path': '', 'version': '', 'logged_in': False, 'account': '', 'quota': None,
              'workspace': '', 'error': ''}
    try: command = locate()
    except FileNotFoundError as exc:
        report['error'] = str(exc); return report, None
    report.update(installed=True, path=command[0])
    try:
        version = runner(command, ['--version'], timeout=20)
        report['version'] = (re.search(r'\d+\.\d+\.\d+', version.stdout or '') or [''])[0]
        auth = parse_json(runner(command, ['auth', 'status'], timeout=20).stdout)
        report['workspace'] = str(find(auth, 'workspace') or '')
        if find(auth, 'logged_in') is not True:
            report['error'] = '尚未授权：请在管理后台点击「重新授权 / 登录」'; return report, command
        me = runner(command, ['+me'], timeout=30)
        body = parse_json(me.stdout)
        if me.returncode != 0 or body.get('ok') is not True:
            report['error'] = '授权已失效：请在管理后台点击「重新授权 / 登录」' if me.returncode == 3 else '无法读取账号信息（+me 失败）'
            return report, command
        quota = find(body, 'daily_send_quota')
        report.update(logged_in=True, account=primary_account(body), quota=quota if isinstance(quota, (int, str)) else None)
    except subprocess.TimeoutExpired:
        report['error'] = 'agently-cli 响应超时'
    except OSError as exc:
        report['error'] = f'无法启动 agently-cli：{type(exc).__name__}'
    return report, command


def send(command, job, runner=invoke):
    """Send one claimed job; return only structured flags, never CLI output."""
    with tempfile.TemporaryDirectory(prefix='product-monitor-mail-') as directory:
        Path(directory, 'body.html').write_text(job['html'], encoding='utf-8')
        args = ['message', '+send', '--subject', job['subject'], '--body-file', './body.html', '--body-format', 'html', '--confirmed']
        for address in dict.fromkeys(job['to']):
            if not re.fullmatch(r'[^@\s,;]+@[^@\s,;]+', address): return {'returncode': 2, 'ok': False}
            args += ['--to', address]
        try: result = runner(command, args, cwd=directory)
        except subprocess.TimeoutExpired: return {'returncode': None, 'error': 'timeout'}
        except OSError: return {'returncode': None, 'error': 'oserror'}
    body = parse_json(result.stdout)
    return {'returncode': result.returncode, 'ok': body.get('ok') is True,
            'queued': body.get('queued') is True or find(body.get('data') or {}, 'queued') is True,
            'confirmation_required': bool(find(body, 'confirmation_required'))}


LOG = None


def out(text, error=False):
    """print() is unavailable under pythonw.exe (Scheduled Task), so log to a file when asked."""
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + text
    if LOG:
        with open(LOG, 'a', encoding='utf-8') as handle: handle.write(line + '\n')
    elif sys.stdout is not None:
        print(text, file=sys.stderr if error else sys.stdout, flush=True)


URL = re.compile(r'https://[^\s"\'<>`]+')
CODE = re.compile(r'(?i:user[ _-]?code|verification code|device code|验证码|授权码|code)\s*[:：=]\s*["\']?([A-Z0-9]{3,8}(?:-[A-Z0-9]{3,8})?)\b')
LOGIN_TIMEOUT = 15 * 60


class AuthFlow:
    """One `agently-cli auth login` device flow. Raw CLI output is kept in memory only."""
    def __init__(self, command, flow_id, popen=subprocess.Popen):
        self.id, self.started, self.text = flow_id, time.time(), []
        self.url, self.code = '', ''
        self.process = popen(command + ['auth', 'login'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=cli_env(), creationflags=NO_WINDOW)
        import threading
        self.reader = threading.Thread(target=self._read, daemon=True); self.reader.start()

    def _read(self):
        for raw in iter(self.process.stdout.readline, b''):
            line = raw.decode('utf-8', errors='replace')
            self.text.append(line)
            if not self.url:
                match = URL.search(line)
                if match: self.url = match.group(0).rstrip('.,;)）。')
            if not self.code:
                match = CODE.search(line)
                if match and not URL.search(match.group(0)): self.code = match.group(1)

    def state(self):
        code = self.process.poll()
        if code is None:
            if time.time() - self.started > LOGIN_TIMEOUT:
                self.cancel(); return 'expired', '授权链接已过期，请重新点击「重新授权 / 登录」'
            return ('waiting', '请点击或复制以下链接在浏览器中完成授权：') if self.url else ('running', '正在向 Agent Mail 请求授权链接…')
        self.reader.join(timeout=2)
        if code == 0: return 'finishing', '授权已完成，正在读取账号信息…'
        return 'failed', f'授权未完成（agently-cli 退出码 {code}）；可再次点击「重新授权 / 登录」'

    def cancel(self):
        if self.process.poll() is None:
            self.process.kill()


def acquire_lock(path):
    """Keep a single worker per data directory (Scheduled Task + manual starts)."""
    handle = open(path, 'a+')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close(); return None
    return handle


class Api:
    def __init__(self, server, token):
        self.server, self.token = server.rstrip('/'), token

    def post(self, path, payload=None):
        request = urllib.request.Request(self.server + '/api/agentmail/worker/' + path, method='POST',
            data=json.dumps(payload or {}, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'X-Agentmail-Token': self.token})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))


def describe(report):
    if not report['installed']: return report['error']
    if not report['logged_in']: return report['error'] or '尚未授权'
    return f"工作进程运行中 · CLI {report['version']} · 发件账号 {report['account']}"


def tick(api, command, report, runner=invoke, limit=5):
    """Process up to `limit` jobs. Only called when the CLI is installed and authorized."""
    sent = 0
    for _ in range(limit):
        job = api.post('claim').get('job')
        if not job: break
        result = send(command, job, runner)
        api.post('result', {'job': job['job'], 'result': result})
        sent += 1
        if result.get('returncode') == 3: report['logged_in'] = False  # re-probe before claiming again
        if result.get('returncode') in (3, 7): break  # auth lost / rate limited: stop this round
    return sent


def handle_auth(api, command, flow, report):
    """Start/advance/cancel a login or logout requested from the admin page. Returns (flow, report_changed)."""
    request = api.post('command').get('command') or {}
    if flow and (request.get('id') != flow.id or request.get('cancel')):
        flow.cancel()
        api.post('auth', {'id': flow.id, 'state': 'cancelled', 'message': '已取消授权'})
        return None, False
    if not flow and request.get('state') == 'requested':
        if not command:
            api.post('auth', {'id': request['id'], 'state': 'failed', 'message': report.get('error') or '未安装 agently-cli'})
            return None, False
        if request.get('action') == 'logout':
            api.post('auth', {'id': request['id'], 'state': 'running', 'message': '正在退出登录…'})
            try: result = invoke(command, ['auth', 'logout'], timeout=30)
            except (OSError, subprocess.SubprocessError): result = None
            ok = result is not None and result.returncode == 0
            api.post('auth', {'id': request['id'], 'state': 'done' if ok else 'failed',
                              'message': '已退出登录；可点击「重新授权 / 登录」切换账号' if ok else '退出登录失败'})
            return None, True
        try: flow = AuthFlow(command, request['id'])
        except OSError:
            api.post('auth', {'id': request['id'], 'state': 'failed', 'message': '无法启动 agently-cli auth login'}); return None, False
        out('已开始 Agent Mail 授权流程（来自管理后台）')
    if not flow: return None, False
    state, message = flow.state()
    if state == 'finishing':
        return flow, True  # caller re-probes, then reports done/failed with the account
    api.post('auth', {'id': flow.id, 'state': state, 'message': message, 'url': flow.url, 'user_code': flow.code})
    if state in ('failed', 'expired'): return None, False
    return flow, False


def main(argv=None):
    global LOG
    parser = argparse.ArgumentParser(description='商品监控 Agent Mail 本机工作进程')
    parser.add_argument('--server', default=os.getenv('MONITOR_URL', 'http://127.0.0.1:8080'))
    parser.add_argument('--token-file', default=str(ROOT / 'data' / 'agentmail-worker.token'))
    parser.add_argument('--interval', type=int, default=10)
    parser.add_argument('--log', help='写入日志文件（pythonw / 计划任务下使用）')
    parser.add_argument('--once', action='store_true', help='处理一次队列后退出（可能发送已排队邮件）')
    parser.add_argument('--status', action='store_true', help='只上报 CLI 安装/授权状态，不发送任何邮件')
    args = parser.parse_args(argv)
    LOG = args.log
    token_file = Path(args.token_file)
    oneshot = args.status or args.once
    lock = None
    if not oneshot:
        lock = acquire_lock(token_file.parent / 'agentmail-worker.lock')
        if lock is None:
            out('已有 Agent Mail 工作进程在运行；本次不再启动。'); return 0
    host = platform.node()
    # Wait for the monitor (e.g. Docker still starting at logon) instead of exiting.
    while True:
        try:
            if not token_file.is_file(): raise FileNotFoundError(str(token_file))
            api = Api(args.server, token_file.read_text(encoding='ascii').strip())
            report, command = probe()
            api.post('heartbeat', {**report, 'message': describe(report), 'host': host, 'probe_only': args.status})
            break
        except urllib.error.HTTPError as exc:
            out(f'监控服务拒绝连接（HTTP {exc.code}）；请确认令牌文件来自正在运行的服务。', True)
        except FileNotFoundError:
            out(f'找不到 {token_file}；请先启动监控服务（docker compose up -d monitor）。', True)
        except OSError:
            out(f'无法连接监控服务 {args.server}；请先启动 docker compose。', True)
        if oneshot: return 2
        time.sleep(15)
    out(describe(report))
    if args.status: return 0 if report['logged_in'] else 3
    out('Agent Mail 工作进程已启动；未授权时也保持运行，以便从管理后台发起登录。')
    last_probe = last_beat = time.time()
    flow = None
    try:
        while True:
            try:
                flow, changed = handle_auth(api, command, flow, report)
                if changed or time.time() - last_probe > 300 or (not report['logged_in'] and time.time() - last_probe > 60):
                    report, command = probe(); last_probe = time.time()
                    api.post('heartbeat', {**report, 'message': describe(report), 'host': host}); last_beat = time.time()
                    if flow and changed:
                        ok = report['logged_in']
                        api.post('auth', {'id': flow.id, 'state': 'done' if ok else 'failed', 'account': report['account'],
                                          'message': f"已授权：{report['account']}" if ok else (report['error'] or '授权未生效')})
                        out('授权流程结束：' + ('成功 ' + report['account'] if ok else '未生效'))
                        flow = None
                if time.time() - last_beat >= args.interval:
                    api.post('heartbeat', {**report, 'message': describe(report), 'host': host}); last_beat = time.time()
                    if report['logged_in'] and command and not flow:
                        tick(api, command, report)
            except OSError as exc:
                out(f'与监控服务通信失败：{type(exc).__name__}；稍后重试', True)
            if args.once: return 0
            time.sleep(2)
    except KeyboardInterrupt:
        return 0
    finally:
        if flow: flow.cancel()
        try: api.post('heartbeat', {**report, 'message': '工作进程已停止：' + describe(report), 'host': host, 'stopping': True})
        except OSError: pass
        if lock: lock.close()


if __name__ == '__main__': raise SystemExit(main())

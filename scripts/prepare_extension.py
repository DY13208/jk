"""Prepare local extension authentication; never print the token or include it in source packages."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    source=ROOT/'data'/'extension-worker.token'
    if not source.exists(): raise SystemExit('请先重新部署并启动后台，生成扩展专用令牌。')
    target=ROOT/'chrome-extension'/'config.local.json'
    target.write_text(json.dumps({'server':'http://127.0.0.1:8080','token':source.read_text().strip()}),encoding='utf-8')
    print('Extension configuration prepared. Load unpacked directory: '+str(target.parent))

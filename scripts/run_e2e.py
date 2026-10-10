"""Local Docker Desktop integration runner; API key remains in process memory."""
import json
import os
import subprocess

import pytest


if __name__ == '__main__':
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'changedetection', 'cat',
                             '/datastore/changedetection.json'], capture_output=True, check=True)
    state = json.loads(result.stdout)
    os.environ['CD_API_KEY'] = state['settings']['application']['api_access_token']
    os.environ['RUN_CD_INTEGRATION'] = '1'
    raise SystemExit(pytest.main(['tests/test_e2e.py', '-q', '--tb=short']))

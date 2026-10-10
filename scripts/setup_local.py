"""Provision local Compose without displaying or persisting the API key."""
import json
import os
import subprocess
from configure import main


if __name__ == '__main__':
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'changedetection', 'cat',
                             '/datastore/changedetection.json'], capture_output=True, check=True)
    os.environ['CD_API_KEY'] = json.loads(result.stdout)['settings']['application']['api_access_token']
    main()

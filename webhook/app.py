
from flask import Flask, request, jsonify
import subprocess
import logging
import os
import threading
import time
from pathlib import Path

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)

# Recovery protection
recovery_lock = threading.Lock()
last_recovery_time = 0
RECOVERY_COOLDOWN = 60  # seconds

PLAYBOOK = os.environ.get(
    "ANSIBLE_PLAYBOOK",
    str(Path(__file__).resolve().parent.parent / "ansible" / "restart-nginx.yml")
)

@app.route('/webhook', methods=['POST'])
def receive_alert():

    global last_recovery_time

    data = request.get_json(silent=True) or {}

    # Find a matching firing alert
    matching_alert = any(
        alert.get('labels', {}).get('alertname') == 'NginxDown'
        and alert.get('status') == 'firing'
        for alert in data.get('alerts', [])
    )

    if not matching_alert:
        return jsonify({
            "status": "ignored",
            "message": "No matching firing NginxDown alert"
        }), 200

    # Prevent concurrent recovery executions
    if not recovery_lock.acquire(blocking=False):
        logging.warning("Recovery already running. Duplicate alert ignored.")

        return jsonify({
            "status": "already_running"
        }), 202

    try:
        current_time = time.monotonic()

        # Prevent repeated recovery during cooldown
        if current_time - last_recovery_time < RECOVERY_COOLDOWN:
            logging.warning("Recovery cooldown active. Alert ignored.")

            return jsonify({
                "status": "cooldown_active"
            }), 200

        # Record recovery attempt
        last_recovery_time = current_time

        logging.info("NGINX failure detected. Starting recovery.")

        result = subprocess.run(
            [
                'sudo',
                '/usr/bin/ansible-playbook',
                '-i', 'localhost,',
                PLAYBOOK
            ],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            logging.error("Recovery failed: %s", result.stderr)

            return jsonify({
                "status": "recovery_failed"
            }), 500

        logging.info("Ansible output:\n%s", result.stdout)
        logging.info("NGINX recovery completed successfully.")

        return jsonify({
            "status": "recovery_successful"
        }), 200

    except subprocess.TimeoutExpired:
        logging.error("Ansible recovery timed out.")

        return jsonify({
            "status": "recovery_timeout"
        }), 500

    except Exception:
        logging.exception("Unexpected recovery error.")

        return jsonify({
            "status": "internal_error"
        }), 500

    finally:
        recovery_lock.release()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)


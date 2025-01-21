import requests
from flask import Flask, request, jsonify
import json
import os
import tempfile
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables for secrets
AUTH_TOKEN = os.environ['AUTH_TOKEN']
PUSHOVER_API_TOKEN = os.environ['PUSHOVER_API_TOKEN']
PUSHOVER_USER_KEY = os.environ['PUSHOVER_USER_KEY']

@app.route('/pushover-webhook', methods=['POST', 'GET'])
def pushover_webhook():
    # Authentication
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f'Bearer {AUTH_TOKEN}':
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == 'POST':
        # Check content type and parse accordingly
        if request.content_type == 'application/json':
            data = request.json
        elif request.content_type == 'application/x-www-form-urlencoded':
            data = request.form
        elif request.content_type.startswith('text/plain'):
            try:
                data = json.loads(request.data.decode('utf-8'))
            except json.JSONDecodeError:
                return jsonify({"error": "Invalid JSON format in text/plain content"}), 400
        else:
            return jsonify({"error": "Unsupported Media Type", "content_type": request.content_type}), 415

        # Fallbacks if fields are empty
        item_name = data.get('ItemName', 'Unknown Item')
        series_name = data.get('SeriesName', '')
        item_type = data.get('ItemType', 'Unknown Type')
        event_id = data.get('EventId', 'Unknown Event')
        item_overview = data.get('ItemOverview', 'No description provided')

        # Title logic
        if series_name:
            title = f"{event_id} - {series_name}: {item_name}"
        else:
            title = f"{event_id} - {item_type}: {item_name}"

        # The body will be the ItemOverview (description)
        body = item_overview

        # Construct the image URL from Jellyfin
        image_url = f"http://192.168.100.21:8096/Items/{data.get('ItemId')}/Images/Primary"

        # Download the image from the Jellyfin server
        try:
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            img_data = response.content
        except requests.exceptions.RequestException as e:
            return jsonify({"error": "Failed to download image", "details": str(e)}), 500
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_img:
            temp_img.write(img_data)
            temp_img_path = temp_img.name

        # Send the notification with image attachment to Pushover
        try:
            pushover_response = requests.post(
                "https://api.pushover.net/1/messages.json",
                data={
                    "token": PUSHOVER_API_TOKEN,
                    "user": PUSHOVER_USER_KEY,
                    "message": body,
                    "title": title
                },
                files={
                    "attachment": ("item_image.jpg", open(temp_img_path, "rb"), "image/jpeg")
                },
                timeout=10
            )
            pushover_response.raise_for_status()
        except requests.exceptions.RequestException as e:
            return jsonify({"error": "Failed to send Pushover notification", "details": str(e)}), 500
        finally:
            os.remove(temp_img_path)  # Cleanup temporary file

        return jsonify({"status": "received POST", "pushover_response": pushover_response.text}), 200

    elif request.method == 'GET':
        return jsonify({"status": "received GET", "message": "This is a webhook endpoint, use POST requests"}), 200

if __name__ == '__main__':
    host = os.environ.get('FLASK_RUN_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_RUN_PORT', 6969))
    app.run(host=host, port=port)
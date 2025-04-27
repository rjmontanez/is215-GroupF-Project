import os
import time
import boto3
from flask import Flask, request, render_template
from werkzeug.utils import secure_filename
from botocore.exceptions import ClientError
from werkzeug.middleware.proxy_fix import ProxyFix

# COLOR SECTION 
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"
# END COLOR SECTION 

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
s3_client = boto3.client('s3', region_name='us-east-1')

BUCKET_NAME = 's215-news-image-buckets'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
WAIT_TIMEOUT = 15
#WAIT_TIMEOUT = 60 #Para mas matagal sana sir, more time for lambda function to work.
POLL_INTERVAL = 1

S3_BASE_URL = f"https://{BUCKET_NAME}.s3.us-east-1.amazonaws.com"

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def wait_for_article(article_key, timeout=WAIT_TIMEOUT):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            s3_client.head_object(Bucket=BUCKET_NAME, Key=article_key)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == "404":
                time.sleep(POLL_INTERVAL)
            else:
                raise e
    return False

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html', title=None, article=None, image_url=None)

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return render_template('index.html', title="Error", article="No file part", image_url=None)

    file = request.files['file']
    if file.filename == '':
        return render_template('index.html', title="Error", article="No selected file", image_url=None)

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        image_key = f'uploads/{filename}'
        article_key = f'articles/{os.path.splitext(filename)[0]}.txt'
        image_url = f'{S3_BASE_URL}/{image_key}'

        # ADD LOG HEADER BEFORE UPLOAD 
        print(f"{YELLOW}===== 🖼️ New Upload Request Received ====={RESET}")
        start = time.time()
        # 🔥 END LOG HEADER 🔥
        

        try:
            s3_client.upload_fileobj(file, BUCKET_NAME, image_key)
            print(f"{YELLOW}⏱ After uploading file to S3: {time.time() - start:.2f} seconds{RESET}")
            if wait_for_article(article_key):
                print(f"{YELLOW}⏱ After waiting for article generation: {time.time() - start:.2f} seconds{RESET}")
                response = s3_client.get_object(Bucket=BUCKET_NAME, Key=article_key)
                print(f"{YELLOW}⏱ After fetching article from S3: {time.time() - start:.2f} seconds{RESET}")
                content = response['Body'].read().decode('utf-8')

                lines = content.strip().split('\n', 1)
                title = lines[0].strip()
                article = lines[1].strip() if len(lines) > 1 else "(No additional article content provided.)"

                print(f"{GREEN}✅ Total time to process upload: {time.time() - start:.2f} seconds{RESET}")

                return render_template('index.html', title=title, article=article, image_url=image_url)
            else:
                return render_template('index.html', title="Timeout", article="Timed out waiting for article generation.", image_url=image_url)
                 print(f"{RED}❌ Timeout while waiting for article. Elapsed time: {WAIT_TIMEOUT:.2f} seconds{RESET}")
        except Exception as e:
            print(f"{RED}❌ An error occurred: {str(e)}{RESET}")
            return render_template('index.html', title="Error", article=f"An error occurred: {str(e)}", image_url=None)

    return render_template('index.html', title="Error", article="Invalid file type", image_url=None)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

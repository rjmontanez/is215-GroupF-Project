import json
import boto3
import requests
import os
import logging
from botocore.exceptions import ClientError, BotoCoreError
import urllib.parse
from typing import Dict, Any, List, Optional

# Constants
REKOGNITION_REGION = 'us-east-1'
S3_REGION = 'us-east-1'
OPENAI_API_ENDPOINT = "https://is215-openai.upou.io/v1/chat/completions"
OPENAI_MODEL = "gpt-3.5-turbo"
ARTICLE_CONTENT_TYPE = 'text/plain; charset=utf-8'
ARTICLE_PREFIX = "articles/"
MAX_LABELS = 10

# Logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS initialization
try:
    rekognition = boto3.client('rekognition', region_name=REKOGNITION_REGION)
    s3 = boto3.client('s3', region_name=S3_REGION)
except BotoCoreError as e:
    logger.error(f"Error initializing AWS clients: {e}")
    rekognition = None
    s3 = None

# Check environment variable for API key
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    logger.error("❌ OpenAI API Key (OPENAI_API_KEY) environment variable not set.")

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:

    # AWS initialization check
    if not rekognition or not s3:
        logger.error("❌ AWS clients not initialized.")
        return {'statusCode': 500, 'body': json.dumps("Internal server error: AWS client initialization failed.")}

    # Extract event data
    try:
        record = event['Records'][0]
        s3_info = record['s3']
        bucket = s3_info['bucket']['name']
        key = urllib.parse.unquote_plus(s3_info['object']['key'], encoding='utf-8')
        logger.info(f"Processing s3://{bucket}/{key}")
    except (KeyError, IndexError, TypeError) as e:
        logger.error(f"❌ Error parsing S3 event structure: {e}. Event: {json.dumps(event)}")
        return {'statusCode': 400, 'body': json.dumps("Invalid S3 event structure.")}

    s3_image_location = {'S3Object': {'Bucket': bucket, 'Name': key}}

    try:
        # Detect labels
        rekognition_response = rekognition.detect_labels(
            Image=s3_image_location,
            MaxLabels=MAX_LABELS
        )
        labels: List[str] = [label['Name'] for label in rekognition_response.get('Labels', []) if 'Name' in label]
        logger.info(f"Detected labels: {labels}")

        if not labels:
            logger.warning("No labels detected in the image.")
            raise ValueError("No labels detected in the image.")

        # Detect faces 
        face_response = rekognition.detect_faces(
            Image=s3_image_location,
            Attributes=['ALL']
        )
        face_details: List[Dict] = face_response.get('FaceDetails', [])
        face_count: int = len(face_details)
        logger.info(f"Detected {face_count} face(s).")

        # Recognize celebrities
        celebrities: List[str] = []
        if face_count > 0:
            celeb_response = rekognition.recognize_celebrities(Image=s3_image_location)
            celebrities = [celeb['Name'] for celeb in celeb_response.get('CelebrityFaces', []) if 'Name' in celeb]
            if celebrities:
                logger.info(f"Recognized celebrities: {', '.join(celebrities)}")
            else:
                logger.info("No celebrities recognized.")
        else:
             logger.info("Skipping celebrity recognition no faces detected.")

        # No Information check
        if not labels and not face_count and not celebrities:
             logger.error("❌ No information extracted from the image.")
             return {'statusCode': 400, 'body': json.dumps("Could not extract information from the image.")}

        # Construct prompt
        prompt_parts: List[str] = []
        if labels:
            prompt_parts.append(f"Image labels: {', '.join(labels)}.")
        if face_count:
            prompt_parts.append(f"{face_count} face(s) detected.")
        if celebrities:
            prompt_parts.append(f"Recognized celebrity faces: {', '.join(celebrities)}.")

        image_context = " ".join(prompt_parts)
        full_context = (
            f"Write a short, engaging, news-style article based on the following information extracted from an image: {image_context} "
            f"Start the article with a compelling title on the very first line, followed by the article body in paragraphs."
        )
        logger.info(f"Constructed OpenAI prompt: {full_context}")

        payload = {
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant acting as a journalist describing images."},
                {"role": "user", "content": full_context}
            ]
        }

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        logger.info(f"Sending request to OpenAI endpoint: {OPENAI_API_ENDPOINT}")
        response = requests.post(OPENAI_API_ENDPOINT, headers=headers, json=payload, timeout=30) # Added timeout
        response.raise_for_status()

        content = response.json()

        if not isinstance(content, dict) or 'choices' not in content or not isinstance(content['choices'], list) or not content['choices']:
             logger.error(f"❌ Unexpected OpenAI response format: {content}")
             raise ValueError("OpenAI returned an invalid or empty response structure.")

        message = content['choices'][0].get('message')
        if not isinstance(message, dict) or 'content' not in message:
            logger.error(f"❌ Missing 'message' or 'content' in OpenAI choice: {content['choices'][0]}")
            raise ValueError("OpenAI response choice is missing message content.")

        article: str = message['content'].strip()

        if not article:
             logger.error("❌ OpenAI returned an empty article.")
             raise ValueError("OpenAI returned an empty article string.")

        logger.info("Received article from OpenAI.")

        # Store article in S3 bucket
        base_filename = os.path.splitext(os.path.basename(key))[0]
        output_key = f"{ARTICLE_PREFIX}{base_filename}_article.txt"

        logger.info(f"Saving article to s3://{bucket}/{output_key}")
        s3.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=article.encode('utf-8'),
            ContentType=ARTICLE_CONTENT_TYPE
        )
        success_message = f"✅ Article successfully generated and saved: s3://{bucket}/{output_key}"
        logger.info(success_message)

        return {
            'statusCode': 200,
            'body': json.dumps({'message': success_message, 'output_key': output_key})
        }

    # Exception Handling
    except (ClientError, BotoCoreError) as e:
        logger.error(f"❌ AWS API Error ({e.response.get('Error', {}).get('Code', 'Unknown')}): {e}")
        return {'statusCode': 502, 'body': json.dumps(f"AWS API Error: {e}")}

    except requests.exceptions.Timeout:
        logger.error("❌ OpenAI API request timed out.")
        return {'statusCode': 504, 'body': json.dumps("OpenAI API request timed out.")}

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ OpenAI request error: {e}")
        status_code = e.response.status_code if e.response is not None else 500
        error_body = e.response.text if e.response is not None else "OpenAI API request failed"
        return {'statusCode': status_code, 'body': json.dumps(f"OpenAI API Error: {error_body}")}

    except ValueError as ve:
        logger.error(f"❌ Data processing error: {ve}")
        return {'statusCode': 400, 'body': json.dumps(f"Data processing error: {str(ve)}")}

    except Exception as e:
        logger.exception("❌ An unexpected error occurred.")
        return {'statusCode': 500, 'body': json.dumps(f"Unexpected internal server error: {str(e)}")}


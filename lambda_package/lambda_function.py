import json
import boto3
import requests
import os
from botocore.exceptions import ClientError
import urllib.parse

rekognition = boto3.client('rekognition', region_name='us-east-1')
s3 = boto3.client('s3')

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_ENDPOINT = "https://is215-openai.upou.io/v1/chat/completions"

def lambda_handler(event, context):
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'])

    try:
        rekognition_response = rekognition.detect_labels(
            Image={'S3Object': {'Bucket': bucket, 'Name': key}},
            MaxLabels=10
        )
        labels = [label['Name'] for label in rekognition_response.get('Labels', [])]

        if not labels:
            raise ValueError("No labels detected in the image.")

        face_response = rekognition.detect_faces(
            Image={'S3Object': {'Bucket': bucket, 'Name': key}},
            Attributes=['ALL']
        )
        face_count = len(face_response.get('FaceDetails', []))

        celeb_response = rekognition.recognize_celebrities(
            Image={'S3Object': {'Bucket': bucket, 'Name': key}}
        )
        celebrities = [celeb['Name'] for celeb in celeb_response.get('CelebrityFaces', [])]

        additional_info = []
        if face_count:
            additional_info.append(f"{face_count} face(s) detected")
        if celebrities:
            additional_info.append(f"Recognized celebrity faces: {', '.join(celebrities)}")

        full_context = (
            f"Write a news-style article based on these image labels: {', '.join(labels)}. "
            f"{'. '.join(additional_info)}. Start with an engaging title on the first line, then the body in paragraphs."
        )

        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": "You are a journalist writing about images."},
                {"role": "user", "content": full_context}
            ]
        }

        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

        response = requests.post(OPENAI_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()

        content = response.json()
        if 'choices' not in content or not content['choices']:
            raise ValueError("OpenAI returned no results.")

        article = content['choices'][0]['message']['content'].strip()

        output_key = f"articles/{os.path.splitext(os.path.basename(key))[0]}.txt"
        s3.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=article.encode('utf-8'),
            ContentType='text/plain'
        )
        print(f"✅ Article saved: s3://{bucket}/{output_key}")


        return {
            'statusCode': 200,
            'body': json.dumps(f'Article saved as {output_key}')
        }

    except ValueError as ve:
        print(f"❌ ValueError: {ve}")
        return {'statusCode': 400, 'body': json.dumps(str(ve))}

    except requests.exceptions.RequestException as re:
        print(f"❌ OpenAI request error: {re}")
        return {'statusCode': 500, 'body': json.dumps("OpenAI API error")}

    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return {'statusCode': 500, 'body': json.dumps("Unexpected error occurred")}

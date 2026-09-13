import requests
import time
import os

BASE_URL = "http://localhost:8000/api/v1"

print("--- MARKSWALA E2E API TEST ---")

# 1. Register User
print("1. Registering user...")
res = requests.post(f"{BASE_URL}/register", json={
    "email": "teacher1@test.com",
    "password": "password123",
    "full_name": "Test Teacher"
})
if res.status_code == 200:
    print("User registered successfully.")
elif res.status_code == 400 and "already exists" in res.text:
    print("User already exists, proceeding to login.")
else:
    print(f"Register failed: {res.text}")

# 2. Login
print("2. Logging in...")
res = requests.post(f"{BASE_URL}/login/access-token", data={
    "username": "teacher1@test.com",
    "password": "password123"
})
if res.status_code != 200:
    print(f"Login failed: {res.text}")
    exit(1)
    
token = res.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}
print("Login successful.")

# 3. Create Course
print("3. Creating course...")
res = requests.post(f"{BASE_URL}/courses/", headers=headers, json={
    "title": "Data Structures and Algorithms",
    "description": "DSA Course",
    "code": "CS201"
})
if res.status_code != 200:
    print(f"Course creation failed: {res.text}")
    exit(1)

course_id = res.json()["id"]
print(f"Course created with ID: {course_id}")

# 4. Upload Syllabus (RAG)
print("4. Uploading syllabus...")
syllabus_path = "test_data/syllabus.pdf"
with open(syllabus_path, "rb") as f:
    res = requests.post(f"{BASE_URL}/resources/", headers=headers, params={"course_id": course_id}, files={"file": f})

if res.status_code != 200:
    print(f"Syllabus upload failed: {res.text}")
else:
    print("Syllabus uploaded successfully.")

# Wait for RAG ingestion
time.sleep(2)

# 5. Create Exam
print("5. Creating exam...")
res = requests.post(f"{BASE_URL}/exams/", headers=headers, json={
    "title": "Internal Assessment 1",
    "course_id": course_id
})
if res.status_code != 200:
    print(f"Exam creation failed: {res.text}")
    exit(1)

exam_id = res.json()["id"]
print(f"Exam created with ID: {exam_id}")

# 6. Upload Question Paper
print("6. Uploading question paper...")
qpaper_path = "test_data/question_paper.pdf"
with open(qpaper_path, "rb") as f:
    res = requests.post(f"{BASE_URL}/questions/upload", headers=headers, params={"exam_id": exam_id}, files={"file": f})

if res.status_code != 200:
    print(f"Question paper upload failed: {res.text}")
else:
    print("Question paper uploaded. Polling for background parsing to finish (local model)...")

# Poll for the question parser to finish instead of a fixed sleep (local model
# latency varies a lot, especially on the very first call while models/embedders
# are being loaded/downloaded).
questions = []
for _ in range(30):
    res = requests.get(f"{BASE_URL}/questions/", headers=headers, params={"exam_id": exam_id})
    if res.status_code == 200 and res.json():
        questions = res.json()
        break
    time.sleep(10)

# 7. Check Questions and Rubrics
print("7. Fetching parsed questions...")
if not questions:
    print("Failed to fetch questions (or parsing produced none) after polling.")
else:
    print(f"Parsed {len(questions)} questions.")
    for q in questions:
        print(f" - Q{q['question_number']}: {q['text']} [{q['marks']} marks]")
        # Check rubric
        q_id = q['id']
        r_res = requests.get(f"{BASE_URL}/rubrics/question/{q_id}", headers=headers)
        if r_res.status_code == 200:
            rubric = r_res.json()
            crit_sum = sum(c['marks'] for c in rubric['criteria'])
            print(f"   -> Rubric generated with {len(rubric['criteria'])} criteria (sum={crit_sum}, question marks={q['marks']}). Approving it...")
            # Approve rubric (PUT requires the full criteria list + is_approved)
            ap_res = requests.put(f"{BASE_URL}/rubrics/{rubric['id']}", headers=headers, json={
                "is_approved": True,
                "criteria": [{"description": c["description"], "marks": c["marks"], "order": c["order"]} for c in rubric["criteria"]]
            })
            if ap_res.status_code == 200:
                print("   -> Rubric approved.")
            else:
                print(f"   -> Failed to approve rubric: {ap_res.text}")
        else:
            print(f"   -> Failed to fetch rubric: {r_res.text}")

# 8. Upload Answer Sheet
print("8. Uploading Answer Sheet...")
answer_path = "test_data/answer_sheet.pdf"
with open(answer_path, "rb") as f:
    res = requests.post(f"{BASE_URL}/answers/upload", headers=headers, params={"exam_id": exam_id}, files={"file": f})

if res.status_code != 200:
    print(f"Answer sheet upload failed: {res.text}")
else:
    print("Answer sheet uploaded. Polling for OCR + evaluation (local model)...")

queue = []
for _ in range(30):
    res = requests.get(f"{BASE_URL}/answers/review-queue", headers=headers, params={"exam_id": exam_id})
    if res.status_code == 200 and res.json():
        queue = res.json()
        break
    time.sleep(10)

# 9. Check Review Queue / Results
print("9. Fetching review queue...")
if not queue:
    print("Found 0 items in review queue after polling (OCR/mapping/evaluation may have failed - check backend logs).")
else:
    print(f"Found {len(queue)} items in review queue.")
    for item in queue:
        print(f" - Answer to Q{item['question_number']}: AI Score: {item['total_score']}/{item['max_marks']}")
        print(f"   Feedback: {item['feedback']}")
        print(f"   Confidence: {item['confidence']}")

print("--- END OF TEST ---")

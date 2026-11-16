"""Locust load model.  Headless example:
  locust -f load/locustfile.py --headless -u 60 -r 10 -t 120s --host http://localhost:8000

Mix (weights): 10 submit | 3 submit-and-wait (measures real end-to-end) | 2 duplicate (cache hits)
               1 invalid (quarantine) | 1 feedback.  Each simulated user has its own X-API-Key, so the
per-key rate limit behaves like real separate clients. 429 is recorded as its own row, not a failure.
"""
import random
import time
import uuid
from datetime import date, timedelta

from locust import HttpUser, between, events, task

DRUGS = ["Amoxicillin", "Ibuprofen", "Metformin", "Atorvastatin", "Warfarin", "Lisinopril", "Sertraline", "Omeprazole"]
REACTIONS = [  # ~35% serious wording, so the stub's rules exercise both branches
    "Mild headache and slight nausea for a few hours after the dose",
    "Skin rash on both arms that settled after stopping the tablets",
    "Patient was hospitalised with severe vomiting and dehydration",
    "Developed anaphylaxis within an hour and needed adrenaline",
    "Dizziness and fatigue for two days, resolved on its own",
    "Persistent cough and mild fever, no treatment needed",
    "Patient had a seizure and was admitted to intensive care",
]
FIXED = {"reporter_type": "pharmacist", "patient_age_years": 42, "patient_sex": "F", "suspect_drug": "Amoxicillin",
         "reaction_description": "Mild itching and a small rash that settled after stopping the tablets",
         "onset_date": str(date.today() - timedelta(days=3))}


def report():
    return {"reporter_type": random.choice(["physician", "nurse", "pharmacist", "consumer"]),
            "patient_age_years": random.randint(1, 95), "patient_sex": random.choice(["M", "F", "U"]),
            "suspect_drug": random.choice(DRUGS), "dose": f"{random.choice([5, 10, 20, 50, 100])} mg",
            "reaction_description": random.choice(REACTIONS) + f" (case {uuid.uuid4().hex[:6]})",  # unique -> no cache hit
            "onset_date": str(date.today() - timedelta(days=random.randint(0, 60))),
            "outcome": random.choice(["recovered", "recovering", "unknown"])}


class Clinician(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.h = {"X-API-Key": f"locust-{uuid.uuid4().hex[:8]}"}
        self.jobs = []

    def _post(self, body, name):
        with self.client.post("/v1/reports", json=body, headers=self.h, name=name, catch_response=True) as r:
            if r.status_code in (200, 202):
                r.success()
            elif r.status_code == 429:
                r.success()  # expected under rate limiting; visible via pv_reports_total{result="rate_limited"}
            else:
                r.failure(f"{r.status_code}")
            return r

    @task(10)
    def submit(self):
        r = self._post(report(), "POST /v1/reports")
        if r.status_code == 202:
            self.jobs.append(r.json()["job_id"])
            self.jobs = self.jobs[-20:]

    @task(3)
    def submit_and_wait(self):
        """Closed-loop e2e: time from POST to status=done, reported as its own Locust row."""
        t0 = time.perf_counter()
        r = self._post(report(), "POST /v1/reports (e2e)")
        if r.status_code != 202:
            return
        job_id, err = r.json()["job_id"], None
        for _ in range(120):
            time.sleep(0.25)
            g = self.client.get(f"/v1/reports/{job_id}", headers=self.h, name="GET /v1/reports/[id]")
            if g.status_code == 200 and g.json()["status"] in ("done", "failed"):
                break
        else:
            err = Exception("timed out waiting for done (30s)")
        events.request.fire(request_type="E2E", name="submit -> done", response_time=(time.perf_counter() - t0) * 1000,
                            response_length=0, exception=err, context={})

    @task(2)
    def duplicate(self):
        self._post(FIXED, "POST /v1/reports (duplicate)")

    @task(2)
    def poll(self):
        if self.jobs:
            self.client.get(f"/v1/reports/{random.choice(self.jobs)}", headers=self.h, name="GET /v1/reports/[id]")

    @task(1)
    def invalid(self):
        with self.client.post("/v1/reports", json={**report(), "patient_age_years": 999}, headers=self.h,
                              name="POST /v1/reports (invalid)", catch_response=True) as r:
            r.success() if r.status_code in (422, 429) else r.failure(f"expected 422 got {r.status_code}")

    @task(1)
    def feedback(self):
        if self.jobs:
            self.client.post("/v1/feedback", headers=self.h, name="POST /v1/feedback",
                             json={"job_id": random.choice(self.jobs), "verdict": random.choice(["agree"] * 9 + ["disagree"])})

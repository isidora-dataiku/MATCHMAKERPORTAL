import dataiku
import pandas as pd
import json
import uuid
from datetime import datetime
from flask import request, jsonify

# ── helpers ──────────────────────────────────────────────────────────────────

def _now():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _client():
    return dataiku.api_client()

def _project():
    return _client().get_default_project()

# ── GET /api/health ──────────────────────────────────────────────────────────

@app.route("/api/health")
def health():
    try:
        dataiku.Dataset("match_results").get_dataframe(sampling="head", limit=1)
        ready = True
    except Exception:
        ready = False
    return jsonify({"status": "ok", "datasets_ready": ready})

# ── GET /api/candidate?id=C-001 ──────────────────────────────────────────────

@app.route("/api/candidate")
def get_candidate():
    cid = request.args.get("id", "").strip()
    if not cid:
        return jsonify({"error": "id vereist"}), 400
    try:
        df = dataiku.Dataset("candidates_enriched_prepared").get_dataframe()
        row = df[df["candidate_id"] == cid]
        if row.empty:
            return jsonify({"error": f"Kandidaat {cid} niet gevonden"}), 404
        r = row.iloc[0].to_dict()
        # No PII — drop geboortejaar for display
        safe = {k: v for k, v in r.items()
                if k not in ("geboortejaar",) and not (isinstance(v, float) and pd.isna(v))}
        # Normalise JSON string fields
        for field in ("vaardigheden_gestandaardiseerd", "vaardigheden_raw"):
            if field in safe and isinstance(safe[field], str):
                try:
                    safe[field] = json.loads(safe[field])
                except Exception:
                    pass
        return jsonify(safe)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── GET /api/jobs ─────────────────────────────────────────────────────────────

@app.route("/api/jobs")
def get_jobs():
    try:
        df = dataiku.Dataset("job_postings_enriched_prepared").get_dataframe()
        actief = df[df["status"] == "actief"] if "status" in df.columns else df
        cols = ["job_id", "titel", "bedrijf_naam", "bedrijf_sector", "gemeente",
                "contract_type", "uren_per_week_min", "uren_per_week_max",
                "vereiste_opleiding", "functieniveau", "sector_gestandaardiseerd"]
        cols = [c for c in cols if c in actief.columns]
        return jsonify(actief[cols].fillna("").to_dict(orient="records"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── POST /api/match ──────────────────────────────────────────────────────────

@app.route("/api/match", methods=["POST"])
def run_match():
    body = request.get_json(force=True) or {}
    candidate_id = body.get("candidate_id", "").strip()
    if not candidate_id:
        return jsonify({"error": "candidate_id vereist"}), 400

    try:
        project = _project()

        # Call the structured agent via the LLM Mesh
        llm = project.get_llm("agent:88Dxqn0F")
        completion = llm.new_completion()
        completion.with_message(candidate_id)
        response = completion.execute()
        full_text = response.text if response else ""

        # Read freshly written match_results for this candidate
        df = dataiku.Dataset("match_results").get_dataframe()
        # Get the latest session for this candidate
        cand_rows = df[df["candidate_id"] == candidate_id].copy()
        if cand_rows.empty:
            return jsonify({"error": "Agent leverde geen resultaten", "agent_output": full_text}), 500

        latest_session = cand_rows.sort_values("generated_at").iloc[-1]["session_id"]
        session_rows = cand_rows[cand_rows["session_id"] == latest_session].sort_values("rank_in_session")

        matches = []
        for _, row in session_rows.iterrows():
            m = row.to_dict()
            # Sanitise NaN — not valid JSON
            for k, v in list(m.items()):
                if isinstance(v, float) and (v != v):  # NaN check
                    m[k] = None
            # Coerce boolean fields that may have been read back as float
            for bool_field in ("location_fit", "hours_fit", "guardrail_passed"):
                if bool_field in m and m[bool_field] is not None:
                    m[bool_field] = bool(m[bool_field])
            for field in ("top_matching_skills", "missing_skills"):
                if isinstance(m.get(field), str):
                    try:
                        m[field] = json.loads(m[field])
                    except Exception:
                        m[field] = []
            matches.append(m)

        # Enrich matches with job metadata (title, company, location)
        _enrich_matches_with_job_info(matches)

        # Also get candidate profile
        cand_df = dataiku.Dataset("candidates_enriched_prepared").get_dataframe()
        cand_row = cand_df[cand_df["candidate_id"] == candidate_id]
        kandidaat = {}
        if not cand_row.empty:
            kandidaat = {k: v for k, v in cand_row.iloc[0].to_dict().items()
                         if k not in ("geboortejaar",) and not (isinstance(v, float) and pd.isna(v))}

        return jsonify({
            "session_id": latest_session,
            "candidate_id": candidate_id,
            "kandidaat": kandidaat,
            "matches": matches,
        })

    except Exception as e:
        import traceback, logging
        logging.warning(f"Agent call failed for {candidate_id}: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        # Fallback: rule-based scoring
        try:
            resp = _fallback_match(candidate_id)
            # Inject the agent error so the UI can show fallback banner + we can see reason
            data = resp.get_json()
            data["agent_error"] = str(e)
            return jsonify(data)
        except Exception as e2:
            return jsonify({"error": str(e), "fallback_error": str(e2)}), 500


def _enrich_matches_with_job_info(matches):
    """Attach job title, company, and gemeente to each match dict (in-place).
    Always reads from jobs dataset so display is consistent regardless of LLM output."""
    import logging
    if not matches:
        return
    try:
        jobs_df = dataiku.Dataset("job_postings_enriched_prepared").get_dataframe()
        logging.info(f"job_postings_enriched_prepared columns: {list(jobs_df.columns)}")
        logging.info(f"sample job_ids: {list(jobs_df['job_id'].head(3)) if 'job_id' in jobs_df.columns else 'NO job_id column'}")
        job_cols = [c for c in ["job_id", "titel", "bedrijf_naam", "gemeente", "contract_type",
                    "uren_per_week_min", "uren_per_week_max", "functieniveau",
                    "sector_gestandaardiseerd"] if c in jobs_df.columns]
        job_map = jobs_df[job_cols].set_index("job_id").to_dict(orient="index")
        for m in matches:
            jid = str(m.get("job_id", ""))
            info = job_map.get(jid, {})
            logging.info(f"Enriching {jid}: found={bool(info)}, titel={info.get('titel','MISSING')}")
            # Always set job_titel from dataset (overrides whatever LLM may have written)
            titel = info.get("titel", "") or ""
            m["job_titel"] = titel if titel else jid
            for k, v in info.items():
                if k != "titel" and k not in m:
                    m[k] = v if not (isinstance(v, float) and pd.isna(v)) else ""
    except Exception as e:
        import logging as _log
        _log.warning(f"_enrich_matches_with_job_info failed: {e}")


def _fallback_match(candidate_id):
    """Simple overlap-based fallback when agent is unavailable."""
    session_id = str(uuid.uuid4())
    now = _now()

    cand_df = dataiku.Dataset("candidates_enriched_prepared").get_dataframe()
    jobs_df = dataiku.Dataset("job_postings_enriched_prepared").get_dataframe()

    cand_row = cand_df[cand_df["candidate_id"] == candidate_id]
    if cand_row.empty:
        return jsonify({"error": f"Kandidaat {candidate_id} niet gevonden"}), 404

    c = cand_row.iloc[0]
    cand_skills = set()
    try:
        raw = c.get("vaardigheden_gestandaardiseerd", "[]")
        cand_skills = set(json.loads(raw if isinstance(raw, str) else "[]"))
    except Exception:
        pass

    actief = jobs_df[jobs_df.get("status", pd.Series(["actief"]*len(jobs_df))) == "actief"] if "status" in jobs_df.columns else jobs_df
    matches = []
    for _, job in actief.iterrows():
        job_skills = set()
        try:
            raw = job.get("vereiste_vaardigheden_lijst", "[]")
            job_skills = set(json.loads(raw if isinstance(raw, str) else "[]"))
        except Exception:
            pass
        overlap = len(cand_skills & job_skills) / max(len(job_skills), 1)
        matches.append({
            "match_id": str(uuid.uuid4()),
            "session_id": session_id,
            "candidate_id": candidate_id,
            "job_id": job.get("job_id", ""),
            "job_titel": job.get("titel", job.get("job_id", "")),
            "bedrijf_naam": job.get("bedrijf_naam", ""),
            "gemeente": job.get("gemeente", ""),
            "match_score": round(overlap, 2),
            "semantic_score": 0.0,
            "skill_overlap_pct": round(overlap, 2),
            "experience_fit": "match",
            "education_fit": "match",
            "location_fit": True,
            "hours_fit": True,
            "match_summary_nl": f"Vaardigheidsoverlap: {int(overlap*100)}%. (Fallback modus — agent niet beschikbaar)",
            "top_matching_skills": list(cand_skills & job_skills)[:5],
            "missing_skills": list(job_skills - cand_skills)[:5],
            "rank_in_session": 0,
            "generated_at": now,
            "llm_model_version": "fallback",
            "guardrail_passed": True,
        })

    matches.sort(key=lambda x: x["match_score"], reverse=True)
    for i, m in enumerate(matches[:5]):
        m["rank_in_session"] = i + 1

    kandidaat = {k: v for k, v in c.to_dict().items()
                 if k not in ("geboortejaar",) and not (isinstance(v, float) and pd.isna(v))}

    return jsonify({
        "session_id": session_id,
        "candidate_id": candidate_id,
        "kandidaat": kandidaat,
        "matches": matches[:5],
        "fallback": True,
    })


# ── POST /api/feedback ────────────────────────────────────────────────────────

@app.route("/api/feedback", methods=["POST"])
def save_feedback():
    body = request.get_json(force=True) or {}
    required = ["match_id", "session_id", "candidate_id", "job_id", "beslissing"]
    for f in required:
        if not body.get(f):
            return jsonify({"error": f"{f} vereist"}), 400

    beslissing = body["beslissing"]
    if beslissing not in ("geaccepteerd", "afgewezen"):
        return jsonify({"error": "beslissing moet 'geaccepteerd' of 'afgewezen' zijn"}), 400

    now = _now()
    feedback_id = str(uuid.uuid4())

    try:
        # advisor_feedback
        fb_ds = dataiku.Dataset("advisor_feedback")
        existing_fb = fb_ds.get_dataframe()
        new_fb = pd.DataFrame([{
            "feedback_id": feedback_id,
            "match_id": body["match_id"],
            "session_id": body["session_id"],
            "candidate_id": body["candidate_id"],
            "job_id": body["job_id"],
            "advisor_login": body.get("advisor_login", ""),
            "advisor_beslissing": beslissing,
            "afwijzing_reden_code": body.get("afwijzing_reden_code", ""),
            "afwijzing_toelichting": body.get("afwijzing_toelichting", ""),
            "match_score_at_time": float(body.get("match_score_at_time", 0)),
            "match_summary_at_time": body.get("match_summary_at_time", ""),
            "beslissing_timestamp": now,
            "time_to_decide_seconds": int(body.get("time_to_decide_seconds", 0)),
        }])
        fb_ds.write_with_schema(pd.concat([existing_fb, new_fb], ignore_index=True))

        # audit_log
        audit_ds = dataiku.Dataset("audit_log")
        existing_audit = audit_ds.get_dataframe()
        event_type = "advisor_accept" if beslissing == "geaccepteerd" else "advisor_reject"
        new_audit = pd.DataFrame([{
            "log_id": str(uuid.uuid4()),
            "event_type": event_type,
            "session_id": body["session_id"],
            "advisor_login": body.get("advisor_login", ""),
            "candidate_id": body["candidate_id"],
            "job_id": body["job_id"],
            "match_id": body["match_id"],
            "event_details": json.dumps({
                "reden": body.get("afwijzing_reden_code", ""),
                "toelichting": body.get("afwijzing_toelichting", ""),
                "score": body.get("match_score_at_time", 0),
            }, ensure_ascii=False),
            "timestamp": now,
        }])
        audit_ds.write_with_schema(pd.concat([existing_audit, new_audit], ignore_index=True))

        return jsonify({"success": True, "feedback_id": feedback_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

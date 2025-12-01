# Recommendation Learning – Context Guide

This document is a quick refresher for how the recommendation learning engine works, how it relates to exploration/recommendation, and how it is wired to the Thin Slice app.

---

## 1. Purpose

- `yummi-server/app/services/recommendationlearning.py` is the background process that periodically refreshes each user’s `latestRecommendations`.
- It mirrors the existing exploration + recommendation pipeline, but conditions on **historical usage**, **feedback**, and **event context** (shopping list + Woolworths cart activity).
- Output is stored on `UserPreferenceProfile.latest_recommendation_meal_ids`, which powers `/v1/recommendations/latest` for the home feed.

---

## 2. High-level flow

1. **Trigger occurs**
   - From the Thin Slice app:
     - When a shopping list is prepared (`shopping_list_build`).
     - When a shopping list is pushed to Woolworths (`woolworths_cart_add`).
     - Or via an explicit learning trigger endpoint.
   - From the backend:
     - Shopping list workflow can request a learning run by setting `triggerRecommendationLearning` on the request.
2. **Run record is scheduled**
   - `schedule_recommendation_learning_run(user_id, trigger, event_context)`:
     - Validates there is an event loop.
     - Schedules `_run_with_guard(...)` as a background task so API responses are not blocked by LLM calls.
3. **Workflow executes**
   - `run_recommendation_learning_workflow(user_id, trigger, event_context)`:
     - Checks `Settings.recommendation_learning_enabled` and `openai_api_key`.
     - Loads the meals manifest (`get_meal_manifest`) and tag manifest (`load_tag_manifest`).
     - Collects a **profile + usage snapshot** and **feedback summary**.
     - Builds a filtered candidate pool.
     - Runs **shadow exploration** (parallel archetype-based LLM calls).
     - Runs **shadow recommendation** (final LLM that picks the lineup).
     - Persists a `RecommendationLearningRun` row and updates `UserPreferenceProfile.latest_recommendation_meal_ids`.

---

## 3. Relation to Exploration and Recommendation

**Exploration (`exploration.py`)**
- Entry: `run_exploration_workflow(user_id, request)`.
- Steps:
  - Enforce OpenAI config.
  - Filter candidates via `generate_candidate_pool_with_details`.
  - Serialize preference profile for prompting.
  - Group candidates into archetype batches with `_build_archetype_batches`.
  - Score batches with `_score_archetype_batches` (parallel LLM calls with streaming).
  - Materialize/rehydrate meals via `_materialize_meals`.
  - Balance across archetypes via `_balance_archetype_meals`.
- Output: `ExplorationRunResponse` with a set of **exploration meals** used for UX and as inputs into recommendation.

**Recommendation (`recommendation.py`)**
- Entry: `run_recommendation_workflow(user_id, request)`.
- Steps:
  - Enforce manifest version (`mealVersion`) and OpenAI config.
  - Load profile and optional `MealExplorationSession`.
  - Record feedback from the user’s reactions to the current feed.
  - Build a candidate pool with constraints and declined IDs (includes excluding latest home-feed meals).
  - Optionally incorporate streamed exploration meals.
  - Call an LLM to rank/select recommendations.
  - Hydrate to `RecommendationMeal` objects and persist to `latestRecommendations` via `update_latest_recommendations`.
- Output: `RecommendationRunResponse` served by `/v1/recommendations/feed` and `/v1/recommendations/latest`.

**Recommendation Learning (`recommendationlearning.py`)**
- Designed to **mimic** exploration + recommendation, but:
  - Runs **asynchronously in the background** in response to usage events.
  - Uses **shadow exploration** and **shadow recommendation** stages instead of the interactive endpoints.
  - Incorporates **historical usage + feedback + event context** into both stages.
  - Writes its results into the same `latestRecommendations` fields as the regular recommendation feed.

---

## 4. Triggers and event context

### 4.1 Trigger types

- `SHOPPING_LIST_TRIGGER = "shopping_list_build"`
- `WOOLWORTHS_CART_TRIGGER = "woolworths_cart_add"`
- Represented by `RecommendationLearningTrigger` in `schemas.py`.

### 4.2 Thin Slice wiring

**App-side helper**
- In `thin-slice-app/App.js`:
  - `triggerRecommendationLearning({ trigger, context, metadata })`:
    - POSTs to `SHOPPING_LIST_LEARNING_ENDPOINT` (`/v1/shopping-list/learning/trigger`).
    - Includes:
      - `trigger`: `"shopping_list_build"` or `"woolworths_cart_add"`.
      - `context`: string label such as `"ingredients.getShoppingList"` or `"shoppingList.addToCart"`.
      - `metadata`: structured details (selected meal IDs, cart item counts, etc.).

**Shopping list preparation**
- When a user opens the shopping list from the ingredients screen:
  - If the list is already prepared, the app calls:
    - `triggerRecommendationLearning({ trigger: "shopping_list_build", context: "ingredients.getShoppingList", metadata: { selectedMealIds, shoppingListItemCount } })`.
  - Otherwise, it calls `handleOpenShoppingListConfirm` with `triggerLearning: true`, which:
    - Adds `triggerRecommendationLearning: true` to the **shopping list build** request body.
    - Calls the `/v1/shopping-list/build` endpoint.

**Push shopping list to Woolworths**
- When the user sends the shopping list to Woolworths:
  - If the list is ready, the app:
    - Calls `triggerRecommendationLearning({ trigger: "woolworths_cart_add", context: "shoppingList.addToCart", metadata: { selectedMealIds, cartItemCount, shoppingListItemCount } })`.
    - Starts the runner/cart-fill flow.

### 4.3 Backend endpoints

- `yummi-server/app/routes/shopping.py`:
  - `POST /v1/shopping-list/build`:
    - Handles shopping list creation via `run_shopping_list_workflow`.
    - When `triggerRecommendationLearning` in the request body is `true`, the shopping list service is responsible for scheduling a recommendation learning run.
  - `POST /v1/shopping-list/learning/trigger`:
    - Accepts `RecommendationLearningTriggerRequest` with:
      - `trigger`
      - `context`
      - `metadata`
    - Wraps these into a `RecommendationLearningContext` via `build_learning_context`.
    - Calls `schedule_recommendation_learning_run(...)`.

- `yummi-server/app/routes/orders.py`:
  - Also schedules learning runs around order events (e.g., Woolworths runner flows) using the same helper.

### 4.4 Event context structure

- `RecommendationLearningContext` dataclass:
  - `request`: normalized copy of the incoming context (e.g., trigger, metadata).
  - `response`: optional snapshot of any associated API response (not always present).
  - `metadata`: arbitrary extra fields.
- `build_learning_context(...)`:
  - Normalizes all payloads via `_json_safe` so they can be serialized and stored.

---

## 5. Workflow internals

### 5.1 Run record creation and guards

- Each run is recorded in `RecommendationLearningRun` (see `models.py`).
- `_create_run_record_if_allowed(user_id, trigger, event_context, usage_snapshot)`:
  - Uses `_fingerprint_payload` to compute JSON-normalized fingerprints of:
    - `event_context`
    - `usage_snapshot`
  - Guardrails:
    - If there is an active `pending` run for the user, new runs are **skipped** (`active_run_in_progress`).
    - If the most recent `completed` run with the same trigger has identical fingerprints, the new run is **skipped** (`duplicate_context`).
  - On success:
    - Inserts a `RecommendationLearningRun` row with `status="pending"`.
    - Stores `event_context` and `usage_snapshot`.

### 5.2 Profile and usage snapshot

- `_collect_profile_snapshot(user_id, tag_manifest)`:
  - Loads `UserPreferenceProfile` from the DB (`get_user_preference_profile`).
  - Serializes it via `serialize_preference_profile(..., include_latest_recommendation_details=True)`.
  - Wraps it into a JSON-safe dict via `_json_safe`.
  - Returns `(profile, usage_snapshot_dict)`.
- If **no profile** exists:
  - The entire learning run is skipped early (learning depends on saved preferences).

### 5.3 Feedback summary

- `load_feedback_summary(user_id)` from `meal_feedback.py`:
  - Aggregates likes/dislikes across multiple sources (exploration, recommendation feed, shopping list, etc.).
  - Exposed to prompts via `feedback_summary.serialize_for_prompt()`.
- The feedback summary is injected into both:
  - Shadow exploration profile payload (`learningFeedback`).
  - Shadow recommendation prompt payload (`feedback`).

### 5.4 Candidate pool

- A `CandidateFilterRequest` is built with:
  - `limit=MAX_CANDIDATE_POOL_LIMIT` (upper bound).
  - Additional fields as needed (e.g., manifest version).
- `generate_candidate_pool_with_details(...)`:
  - Applies the same filtering stack as exploration/recommendation:
    - Tag alignment with the user’s preferences.
    - Manifest + archetype metadata.
  - Returns:
    - A filter response (including manifest ID and timestamps).
    - A list of `CandidateMealDetail` records, each including:
      - `archetype_uid`
      - underlying meal dict from the manifest.
- If no candidates are available:
  - The learning run is marked `failed` with `status="skipped"` or a specific error reason.

---

## 6. Shadow exploration stage

- `_run_shadow_exploration(...)`:
  - Determines `per_archetype_limit`:
    - `settings.recommendation_learning_exploration_meal_count`
    - Or `settings.recommendation_learning_meal_count` fallback.
  - Builds `profile_payload` from `usage_snapshot` and augments with:
    - `"learningFeedback"` (from feedback summary).
    - `"learningEventContext"` (normalized event context).
  - Builds archetype batches via:
    - `exploration_build_archetype_batches(candidate_details, per_archetype_limit)`.
  - For each archetype batch:
    - Calls `exploration_score_archetype_batches` with:
      - A `_ShadowExplorationSettings` proxy that points at the learning-specific OpenAI config (`openai_recommendation_learning_exploration_*`).
      - Stream handlers to capture streamed meal IDs.
  - Cancels any leftover tasks after timeout (`recommendation_learning_exploration_timeout_seconds`).
  - Materializes meals for each archetype:
    - Uses `exploration_materialize_meals(...)` to map LLM selections back onto `CandidateMealDetail` records.
  - Balances across archetypes:
    - Via `exploration_balance_archetype_meals(...)` targeting `settings.recommendation_learning_exploration_meal_count`.
  - Returns:
    - `{ "mealIds": [ ...shortlisted meal IDs... ], "notes": [] }`.

---

## 7. Shadow recommendation stage

- `_run_shadow_recommendation(...)`:
  - Determines final target count:
    - `target = settings.recommendation_learning_meal_count`.
  - Builds a candidate payload from the shortlisted meal details:
    - `_build_candidate_payload(candidate_details, limit=len(candidate_details))`:
      - `meal_id`, `name`, `tags`, `archetype_id` for each candidate.
  - Constructs a prompt payload:
    - `userProfile`: full usage snapshot (including preference + latest recommendation info).
    - `feedback`: summarized likes/dislikes.
    - `eventContext`: learning event context from the trigger.
    - `candidates`: the candidate payload.
    - `targetCount`: desired number of recommendations.
  - Calls `_call_model(...)` via `call_openai_responses`:
    - Uses `openai_recommendation_learning_model`.
    - Tuned by `openai_recommendation_learning_max_output_tokens`, `top_p`, `reasoning_effort`.
    - Bounded by `recommendation_learning_timeout_seconds`.
  - Expects JSON:
    - `{ "recommendations": [{"meal_id": "..."}, ...], "notes": ["..."] }`.
  - Normalizes and hydrates meals:
    - `_normalize_selection(...)` extracts ranked meal IDs.
    - `_hydrate_recommendation_meals(...)`:
      - Maps IDs back to `CandidateMealDetail`.
      - Builds `RecommendationMeal` objects.
      - Applies fallback filling if the model returns fewer than `target`.
  - Returns:
    - `{ "meals": [RecommendationMeal...], "notes": [...] }`.

---

## 8. Persisting results

- If either stage fails or yields no meals:
  - `_update_run_record(...)` is called with:
    - `status="failed"` or `status="skipped"`.
    - `error_message` describing the reason.
    - Partial `response_payload` if available.
- On success:
  - `meal_ids = [meal.mealId for meal in final_meals]`.
  - `update_latest_recommendations(session, user_id, meal_ids, manifest_id, generated_at)`:
    - Stores the new lineup IDs and metadata on `UserPreferenceProfile`.
  - `RecommendationLearningRun` is updated:
    - `status="completed"`.
    - `model=settings.openai_recommendation_learning_model`.
    - `response_payload` contains:
      - `exploration`: shadow exploration summary.
      - `recommendations`: `{ "notes": [...], "mealIds": [...] }`.

---

## 9. Configuration knobs (config.py)

- Candidate + count limits:
  - `recommendation_learning_candidate_limit: int`
  - `recommendation_learning_exploration_meal_count: int`
  - `recommendation_learning_meal_count: int`
  - `recommendation_learning_recommendation_candidate_limit: int`
- Exploration-model settings:
  - `openai_recommendation_learning_exploration_model: str`
  - `openai_recommendation_learning_exploration_top_p: float | None`
  - `openai_recommendation_learning_exploration_reasoning_effort: str`
  - `openai_recommendation_learning_exploration_max_output_tokens: int`
  - `recommendation_learning_exploration_timeout_seconds: int`
- Recommendation-model settings:
  - `openai_recommendation_learning_model: str`
  - `openai_recommendation_learning_top_p: float | None`
  - `openai_recommendation_learning_reasoning_effort: str`
  - `openai_recommendation_learning_max_output_tokens: int`
  - `recommendation_learning_timeout_seconds: int`

---

## 10. How to reorient quickly

When resuming work on this area:
- Skim this file to recall:
  - Triggers (`shopping_list_build`, `woolworths_cart_add`).
  - The two-stage “shadow exploration + shadow recommendation” shape.
  - Where data flows into and out of `RecommendationLearningRun` and `UserPreferenceProfile`.
- Then jump to:
  - `yummi-server/app/services/recommendationlearning.py` for logic.
  - `thin-slice-app/App.js` for client triggers.
  - `yummi-server/app/config.py` for tuning knobs.

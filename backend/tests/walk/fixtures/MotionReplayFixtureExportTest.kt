// Copy into APP's app/src/test/java/com/daengs/app/walk/sync/ temporarily.
// Set MOTION_REPLAY_CASES and MOTION_REPLAY_OUTPUT to absolute JSON paths; run only this class.
// This uses the real APP engine and wire adapter, never the Python implementation.
package com.daengs.app.walk.sync

import android.app.Application
import com.daengs.app.walk.*
import com.daengs.app.walk.motion.*
import java.io.File
import java.time.Instant
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], application = Application::class)
class MotionReplayFixtureExportTest {
    @Test fun export() {
        val data = JSONObject(File(requireNotNull(System.getenv("MOTION_REPLAY_CASES"))).readText())
        val cases = data.getJSONArray("cases")
        for (index in 0 until cases.length()) {
            val case = cases.getJSONObject(index)
            val manifest = case.getJSONObject("manifest")
            val epochs = manifest.getJSONArray("epochs")
            val end = epochs.getJSONObject(epochs.length()-1).getLong("ended_at_millis")
            val id = manifest.getString("client_session_id")
            val raw = RemoteWalkDetail.parse(JSONObject().put("id", "22222222-2222-2222-2222-222222222222")
                .put("client_session_id", id).put("started_at", Instant.ofEpochMilli(epochs.getJSONObject(0).getLong("started_at_millis")).toString())
                .put("ended_at", Instant.ofEpochMilli(end).toString()).put("points", case.getJSONArray("raw_points"))).fixes
            manifest.put("raw_input_fingerprint", WalkRecordingContract.rawFingerprint(raw))
            val plan = WalkMotionContract.read(RecordedSession(id, startedAtMillis = 0, endedAtMillis = end),
                raw, manifest, WalkMotionStore.objects(case.getJSONArray("points")))
            val policy = (MotionPolicies.resolveJson(id, plan.input.session.motionPolicyJson) as MotionPolicySelection.Supported).policy
            val steps = JSONArray()
            val paths = mutableListOf<MutableList<Long>>()
            val reasonCounts = sortedMapOf<String, Int>()
            val result = replayRecordedMotion(policy, plan.input.epochs, plan.input.fixes.asSequence()) { step ->
                step.decision?.let { d ->
                    val e = requireNotNull(step.estimate)
                    fun obj(vararg pairs: Pair<String, Any?>) = JSONObject().also { o -> pairs.forEach { (k,v) -> o.put(k, v ?: JSONObject.NULL) } }
                    steps.put(obj("estimate" to obj("position_quality" to e.positionQuality.name, "speed_mps" to e.speedMps,
                        "speed_source" to e.speedSource.name, "speed_quality" to e.speedQuality.name,
                        "movement" to e.movement.name, "reasons" to JSONArray(e.reasons.map { it.name }.sorted())),
                        "decision" to obj("client_seq" to d.toRef?.ingressSeq, "segment_id" to d.segmentId,
                            "from_seq" to d.fromRef?.ingressSeq, "connection" to d.connection.name,
                            "distance_use" to d.distanceUse.name, "distance_delta_m" to d.distanceDeltaM,
                            "estimated_segment_speed_mps" to d.estimatedSegmentSpeedMps,
                            "reasons" to JSONArray(d.reasons.map { it.name }.sorted()))))
                    d.reasons.forEach { reasonCounts[it.name] = (reasonCounts[it.name] ?: 0) + 1 }
                    if (d.connection == Connection.START_NEW) paths.add(mutableListOf(requireNotNull(d.toRef).ingressSeq))
                    if (d.connection == Connection.CONTINUE) paths.last().add(requireNotNull(d.toRef).ingressSeq)
                }
            }
            case.put("expected", JSONObject().put("distance_m", result.eligibleDistanceM)
                .put("recording_duration_nanos", result.closedRecordingDurationNanos)
                .put("active_duration_millis", result.closedRecordingDurationNanos / 1_000_000)
                .put("point_count", result.processedObservationCount).put("segment_count", result.segmentCount)
                .put("segments", JSONArray(paths.map { JSONArray(it) })).put("reason_counts", JSONObject(reasonCounts)))
            case.put("steps", steps)
            case.put("manifest_fingerprint", plan.manifestHash).put("evidence_fingerprint", plan.evidenceHash)
        }
        File(requireNotNull(System.getenv("MOTION_REPLAY_OUTPUT"))).writeText(data.toString(2)+"\n")
    }
}

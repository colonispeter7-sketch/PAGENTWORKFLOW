"""Explicitly synthetic model boundary for a no-network SDK demonstration."""

from __future__ import annotations

import json

from agents import Model, ModelResponse, Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage

from oversight.review import Analysis, Assessment, Alignment, NextStep, Risk, knowledge_citation


def message(text: str):
    return ResponseOutputMessage(
        id="demo-message", type="message", role="assistant", status="completed",
        content=[{"type": "output_text", "text": text, "annotations": []}],
    )


class DemoModel(Model):
    """Generate a small deterministic report from actual inputs, not AI reasoning.

    The real SDK still executes Lead -> UI tool -> Lead, including structured
    output parsing. Generic prompts/checks deliberately avoid claiming semantic
    understanding. Use an explicitly enabled live run for model assessments.
    """

    def __init__(self, knowledge, diff):
        change = diff.changes[0]
        goal = next(entry for entry in knowledge.entries if entry.status == "approved")
        citations = [change.citations[0], knowledge_citation(goal)]
        analysis = Analysis(
            apparent_intent_unverified=Assessment(
                text="Offline demonstration: the patch appears intended to adjust the listed frontend files; the author's rationale is not supplied.",
                citations=citations,
            ),
            goal_alignment_assessments=[Alignment(
                goal_id=goal.id, assessment="unclear",
                text="The patch affects the sample. A human or live model must assess whether behavior meets this approved objective.",
                citations=citations,
            )],
            potential_risks=[Risk(
                priority="P2", text="A frontend behavior regression is possible; the diff alone does not establish runtime behavior.",
                verification="Inspect each changed event handler and perform a local keyboard and booking demonstration check.",
                citations=[c.citations[0] for c in diff.changes],
            )],
            speculative_recommendations=[Assessment(
                text="Consider a focused regression check for each changed behavior before approving implementation.",
                citations=citations,
            )],
            recommended_next_steps=[NextStep(
                text="Ask Codex to explain the change and a human to check it against the approved goal.",
                acceptance_check="Record goal alignment and repeatable checks; do not approve based on this synthetic demo alone.",
                citations=citations,
            )],
        )
        self.outputs = iter([
            ResponseFunctionToolCall(id="demo-call", type="function_call", call_id="demo-ui",
                                     name="review_ui", arguments=json.dumps({"input": "Review the fixed supplied evidence bundle."})),
            message("UI Agent — synthetic offline review. Changed frontend evidence requires a human usability check. "
                    f"[{change.citations[0]}] No runtime or visual assessment was performed."),
            message(analysis.model_dump_json()),
        ])

    async def get_response(self, **kwargs):
        return ModelResponse(output=[next(self.outputs)], usage=Usage(), response_id=None)

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError("Only bounded non-streaming reviews are supported.")

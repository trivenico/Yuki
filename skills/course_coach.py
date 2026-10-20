"""Course coach scaffolding for learning, labs, revision and interview practice.

This module teaches and guides. It does not submit assessments, access course accounts,
or run commands in a lab environment.
"""

from skills.base import Skill, SkillResult


class CourseCoachSkill(Skill):
    name = "course_coach"
    description = "Explain concepts, guide authorised labs, make revision quizzes, and plan study."
    risk = "low"
    requires_approval = False

    MODES = {"explain", "lab", "quiz", "study_plan", "interview"}

    def run(self, argument=""):
        if not isinstance(argument, str) or not argument.strip():
            return SkillResult(
                False,
                "Use: explain TOPIC, lab TASK, quiz TOPIC, study_plan GOAL, or interview ROLE.",
            )
        mode, _, subject = argument.strip().partition(" ")
        mode = mode.lower()
        subject = subject.strip()
        if mode not in self.MODES or not subject:
            return SkillResult(
                False,
                "Use: explain TOPIC, lab TASK, quiz TOPIC, study_plan GOAL, or interview ROLE.",
            )
        if len(subject) > 450:
            return SkillResult(False, "Keep the topic or goal under 451 characters.")
        if mode == "explain":
            data = {
                "topic": subject,
                "approach": [
                    "Explain the concept in plain language",
                    "Give a concrete example",
                    "Check understanding with a question",
                    "Correct misunderstandings",
                ],
                "note": "This is a learning outline; a connected language model is needed for a tailored explanation.",
            }
        elif mode == "lab":
            data = {
                "task": subject,
                "approach": [
                    "Confirm the lab is authorised and identify its scope",
                    "Ask what you tried and inspect the exact output",
                    "Explain one safe next step at a time",
                    "Verify the result and explain why it worked",
                ],
                "safety": "No commands were run. Do not use these steps against systems without permission.",
            }
        elif mode == "quiz":
            data = {
                "topic": subject,
                "approach": [
                    "Generate practice questions",
                    "Let the learner answer first",
                    "Explain each answer and why distractors are wrong",
                    "Track weak topics",
                ],
                "assessment_boundary": "Practice and explanation only; do not impersonate the learner or submit a graded assessment.",
            }
        elif mode == "study_plan":
            data = {
                "goal": subject,
                "approach": [
                    "Break the goal into weekly topics",
                    "Mix retrieval practice with hands-on labs",
                    "Schedule spaced revision",
                    "Review progress and adjust",
                ],
                "note": "A tailored calendar requires learner availability and progress information.",
            }
        else:
            data = {
                "role": subject,
                "approach": [
                    "Ask one interview question at a time",
                    "Request a concise answer",
                    "Evaluate relevance, structure, technical accuracy and evidence",
                    "Ask a follow-up and finish with a practice plan",
                ],
                "note": "This is the interview workflow; full adaptive evaluation requires a connected language model.",
            }
        return SkillResult(
            True,
            "Course-coaching workflow prepared; no account was accessed and no commands or submissions were performed.",
            data,
        )

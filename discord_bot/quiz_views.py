"""
Quiz UI components for the Discord bot.

Provides interactive Discord Views for the full quiz flow:
  - QuizStartView     : Preview embed + "Start Quiz" button
  - QuizQuestionView  : Per-question UI (MCQ buttons / T/F buttons / text modal)
  - TextAnswerModal   : Modal for short_answer, fill_blank, application questions
  - QuizResultsEmbed  : Rich results embed after grading

State is managed through QuizSession dataclass stored on the user's ConversationSession.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

import discord
from discord import ButtonStyle
from discord.ui import Button, Modal, TextInput, View


# ---------------------------------------------------------------------------
# QuizSession — lightweight state object stored in ConversationSession
# ---------------------------------------------------------------------------

@dataclass
class QuizSession:
    quiz_id: str
    questions: List[Dict[str, Any]]
    num_questions: int
    difficulty: str
    title: str
    # answers keyed by question_id, value varies by type
    answers: Dict[str, Any] = field(default_factory=dict)
    current_index: int = 0
    start_time: float = field(default_factory=time.time)

    @property
    def current_question(self) -> Optional[Dict[str, Any]]:
        if self.current_index < len(self.questions):
            return self.questions[self.current_index]
        return None

    @property
    def is_complete(self) -> bool:
        return len(self.answers) >= len(self.questions)

    @property
    def elapsed_seconds(self) -> int:
        return int(time.time() - self.start_time)


# ---------------------------------------------------------------------------
# Difficulty colours used across embeds
# ---------------------------------------------------------------------------

_DIFF_COLOR = {
    "easy": discord.Color.green(),
    "medium": discord.Color.gold(),
    "hard": discord.Color.red(),
}
_DIFF_EMOJI = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
_TYPE_EMOJI = {
    "multiple_choice": "🔤",
    "true_false": "✅",
    "short_answer": "✍️",
    "fill_blank": "📝",
    "application": "🧠",
}

MCQ_LABELS = ["A", "B", "C", "D"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _question_embed(quiz_session: QuizSession) -> discord.Embed:
    """Build the embed shown above a question's buttons/modal prompt."""
    q = quiz_session.current_question
    idx = quiz_session.current_index
    total = quiz_session.num_questions
    q_type = q.get("question_type", "")

    color = _DIFF_COLOR.get(quiz_session.difficulty, discord.Color.blurple())
    embed = discord.Embed(
        title=f"Question {idx + 1} / {total}",
        description=q.get("question_text", ""),
        color=color,
    )
    type_label = f"{_TYPE_EMOJI.get(q_type, '❓')} {q_type.replace('_', ' ').title()}"
    embed.set_footer(text=f"{quiz_session.title}  •  {type_label}  •  {quiz_session.difficulty.capitalize()}")

    # For MCQ show the options inline so the user can see them alongside buttons
    if q_type == "multiple_choice":
        # The API returns options as a flat field (answer_data is stripped for security)
        options = q.get("options", [])
        options_text = "\n".join(
            f"**{MCQ_LABELS[i]}** — {opt}" for i, opt in enumerate(options)
        )
        embed.add_field(name="Options", value=options_text or "—", inline=False)

    return embed


def _results_embed(quiz_session: QuizSession, grading: Dict[str, Any]) -> discord.Embed:
    """Build the results embed from the grading API response."""
    pct = grading.get("percentage", 0.0)
    total_score = grading.get("total_score", 0)
    max_score = grading.get("max_score", 0)
    scores: Dict[str, Any] = grading.get("scores", {})
    weak_topics: List[str] = grading.get("weak_topics", [])
    suggestions: str = grading.get("suggestions", "")

    MODAL_TYPES = {"short_answer", "fill_blank", "application"}
    has_subjective = any(
        q.get("question_type") in MODAL_TYPES for q in quiz_session.questions
    )

    if pct >= 80:
        color = discord.Color.green()
        medal = "🥇"
        title = "Excellent work!"
    elif pct >= 60:
        color = discord.Color.gold()
        medal = "🥈"
        title = "Good effort!"
    else:
        color = discord.Color.red()
        medal = "🥉"
        title = "Keep practising!"

    score_line = (
        f"**Score: {total_score:.0f} / {max_score:.0f}**"
        if has_subjective
        else f"**Score: {total_score:.0f} / {max_score:.0f}  ({pct:.1f}%)**"
    )

    embed = discord.Embed(
        title=f"{medal} Quiz Complete — {title}",
        description=score_line,
        color=color,
    )

    # ── Per-question review (single field, up to 5 Qs) ──
    MCQ_ALPHA = ["A", "B", "C", "D"]
    review_blocks: List[str] = []

    for i, q in enumerate(quiz_session.questions[:5]):
        qid = str(q.get("id", ""))
        q_score = scores.get(qid, {})
        correct = q_score.get("correct", False)
        q_type = q.get("question_type", "")

        # Modal-type questions: short_answer, fill_blank, application
        IS_MODAL_TYPE = q_type in ("short_answer", "fill_blank", "application")

        icon = "✅" if correct else "❌"
        q_text = q.get("question_text", "")

        # Student's answer
        raw_student = quiz_session.answers.get(qid)
        student_display = q_score.get("student_answer_display")
        if student_display is None:
            if q_type == "multiple_choice" and isinstance(raw_student, int):
                opts = q.get("options", [])
                label = MCQ_ALPHA[raw_student] if raw_student < len(MCQ_ALPHA) else str(raw_student)
                opt_text = opts[raw_student] if raw_student < len(opts) else "?"
                student_display = f"{label}. {opt_text}"
            elif q_type == "true_false":
                student_display = "True" if raw_student else "False"
            else:
                student_display = str(raw_student) if raw_student is not None else "—"

        # Build block — no manual truncation; rely on the 1000-char overall field cap
        block = f"{icon} **Q{i+1}:** {q_text}\n> **Your answer:** {student_display}"

        # Always show correct answer + feedback for modal types; only on wrong for MCQ/T-F
        if IS_MODAL_TYPE or not correct:
            correct_display = q_score.get("correct_answer_display", "—")
            block += f"\n> **Correct answer:** {correct_display}"

        feedback = q_score.get("feedback", "")
        if feedback:
            block += f"\n> **Feedback:** *{feedback}*"
        review_blocks.append(block)

    if review_blocks:
        review_text = "\n\n".join(review_blocks)
        # Hard cap at 1000 chars to stay within Discord field limit
        if len(review_text) > 1000:
            review_text = review_text[:997] + "…"
        embed.add_field(name="📋 Review", value=review_text, inline=False)

    if len(quiz_session.questions) > 5:
        embed.add_field(
            name="\u200b",
            value=f"*…and {len(quiz_session.questions) - 5} more question(s) not shown.*",
            inline=False,
        )

    # ── Weak topics & suggestions ──
    if weak_topics:
        embed.add_field(
            name="📌 Topics to revisit",
            value="\n".join(f"• {t}" for t in weak_topics[:5]),
            inline=False,
        )

    if suggestions:
        embed.add_field(name="💡 Feedback", value=suggestions[:1024], inline=False)

    elapsed = quiz_session.elapsed_seconds
    mins, secs = divmod(elapsed, 60)
    embed.set_footer(text=f"Completed in {mins}m {secs}s  •  {quiz_session.difficulty.capitalize()} difficulty")
    return embed



# ---------------------------------------------------------------------------
# TextAnswerModal — for short_answer, fill_blank, application questions
# ---------------------------------------------------------------------------

class TextAnswerModal(Modal):
    def __init__(
        self,
        quiz_session: QuizSession,
        on_answered: Callable[[str, str], Coroutine],
    ):
        q = quiz_session.current_question
        q_type = q.get("question_type", "text")
        q_text = q.get("question_text", "")
        # Modal title: Discord limit is 45 chars
        super().__init__(title=f"Q{quiz_session.current_index + 1}: {q_type.replace('_', ' ').title()}")

        # Row 0 — full question text shown as pre-filled (default=) so it stays
        # visible while the student types their answer (unlike placeholder which hides)
        self.question_display = TextInput(
            label="Question:",
            style=discord.TextStyle.paragraph,
            default=q_text,
            required=False,
            max_length=4000,
            row=0,
        )
        self.add_item(self.question_display)

        # Row 1 — student's answer
        self.answer_input = TextInput(
            label="Your answer:",
            style=discord.TextStyle.paragraph,
            placeholder="Type your answer here…",
            required=True,
            max_length=2000,
            row=1,
        )
        self.add_item(self.answer_input)
        self.quiz_session = quiz_session
        self.on_answered = on_answered


    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        q = self.quiz_session.current_question
        await self.on_answered(str(q["id"]), self.answer_input.value)


# ---------------------------------------------------------------------------
# QuizQuestionView — shown for each question
# ---------------------------------------------------------------------------

class QuizQuestionView(View):
    """
    Renders the appropriate UI for the current question type:
      - multiple_choice : 4 labelled buttons (A B C D)
      - true_false      : ✅ True / ❌ False buttons
      - others          : A single "Enter answer" button → opens TextAnswerModal
    """

    def __init__(
        self,
        quiz_session: QuizSession,
        on_answered: Callable[[str, Any], Coroutine],
        on_submit_quiz: Callable[[], Coroutine],
    ):
        super().__init__(timeout=300)
        self.quiz_session = quiz_session
        self.on_answered = on_answered
        self.on_submit_quiz = on_submit_quiz
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        q = self.quiz_session.current_question
        if q is None:
            return
        q_type = q.get("question_type", "")
        qid = str(q["id"])

        if q_type == "multiple_choice":
            # API returns options as flat field
            options = q.get("options", [])
            for i, _ in enumerate(options[:4]):
                btn = Button(
                    label=MCQ_LABELS[i],
                    style=ButtonStyle.primary,
                    custom_id=f"mcq_{i}",
                    row=0,
                )
                btn.callback = self._make_mcq_callback(qid, i)
                self.add_item(btn)

        elif q_type == "true_false":
            true_btn = Button(label="✅ True", style=ButtonStyle.success, custom_id="tf_true", row=0)
            false_btn = Button(label="❌ False", style=ButtonStyle.danger, custom_id="tf_false", row=0)
            true_btn.callback = self._make_tf_callback(qid, True)
            false_btn.callback = self._make_tf_callback(qid, False)
            self.add_item(true_btn)
            self.add_item(false_btn)

        else:
            # short_answer / fill_blank / application
            text_btn = Button(
                label="✍️ Enter answer",
                style=ButtonStyle.secondary,
                custom_id="open_modal",
                row=0,
            )
            text_btn.callback = self._open_text_modal
            self.add_item(text_btn)

    def _make_mcq_callback(self, qid: str, selected_index: int):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            await self.on_answered(qid, selected_index)
        return callback

    def _make_tf_callback(self, qid: str, value: bool):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            await self.on_answered(qid, value)
        return callback

    async def _open_text_modal(self, interaction: discord.Interaction):
        modal = TextAnswerModal(self.quiz_session, self.on_answered)
        await interaction.response.send_modal(modal)


# ---------------------------------------------------------------------------
# QuizStartView — shown before the quiz starts
# ---------------------------------------------------------------------------

class QuizStartView(View):
    def __init__(self, on_start: Callable[[], Coroutine]):
        super().__init__(timeout=120)
        self.on_start = on_start

    @discord.ui.button(label="▶ Start Quiz", style=ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, button: Button):
        button.disabled = True
        await interaction.response.defer()
        await self.on_start()


def quiz_preview_embed(quiz_data: Dict[str, Any]) -> discord.Embed:
    """Build the preview embed shown before the user starts the quiz."""
    difficulty = quiz_data.get("difficulty", "medium")
    num_q = quiz_data.get("num_questions", 0)
    title = quiz_data.get("title", "Practice Quiz")
    questions = quiz_data.get("questions", [])
    types = list({q.get("question_type", "") for q in questions})
    types_str = "  ".join(
        f"{_TYPE_EMOJI.get(t, '❓')} {t.replace('_', ' ').title()}" for t in sorted(types)
    )

    embed = discord.Embed(
        title=f"📚 {title}",
        description="Ready to test your knowledge? Click **Start Quiz** when you're ready.",
        color=_DIFF_COLOR.get(difficulty, discord.Color.blurple()),
    )
    embed.add_field(
        name="Details",
        value=(
            f"**Questions:** {num_q}\n"
            f"**Difficulty:** {_DIFF_EMOJI.get(difficulty, '')} {difficulty.capitalize()}\n"
            f"**Types:** {types_str or '—'}"
        ),
        inline=False,
    )
    embed.set_footer(text="Tip: you have 5 minutes per question")
    return embed


# ---------------------------------------------------------------------------
# QuizOrchestrator — wires everything together
# ---------------------------------------------------------------------------

class QuizOrchestrator:
    """
    Manages the full quiz lifecycle for one user/channel:
    generate → preview → per-question UI → submit → results.

    Usage (from bot.py):
        orchestrator = QuizOrchestrator(interaction, session, api_client)
        await orchestrator.start(num_questions=5, difficulty="medium", question_types=[...])
    """

    def __init__(
        self,
        interaction: discord.Interaction,
        api_client,
        user_id: str,
        course_bot_id: str,
        learning_mode: str = "standard",
        ack_message: Optional[discord.Message] = None,
    ):
        self.interaction = interaction
        self.api_client = api_client
        self.user_id = user_id
        self.course_bot_id = course_bot_id
        self.learning_mode = learning_mode
        self.quiz_session: Optional[QuizSession] = None
        # The message we'll keep editing as the quiz progresses.
        # If an ack_message is provided, we edit it for the first update.
        self._message: Optional[discord.Message] = ack_message

    async def start(
        self,
        num_questions: int = 5,
        difficulty: str = "medium",
        question_types: Optional[List[str]] = None,
    ):
        import logging as _logging
        _log = _logging.getLogger(__name__)

        quiz_data = await self.api_client.generate_quiz(
            user_id=self.user_id,
            course_bot_id=self.course_bot_id,
            num_questions=num_questions,
            difficulty=difficulty,
            question_types=question_types,
            learning_mode=self.learning_mode,
        )

        self.quiz_session = QuizSession(
            quiz_id=str(quiz_data["quiz_id"]),
            questions=quiz_data.get("questions", []),
            num_questions=quiz_data.get("num_questions", num_questions),
            difficulty=quiz_data.get("difficulty", difficulty),
            title=quiz_data.get("title", "Practice Quiz"),
        )

        preview = quiz_preview_embed(quiz_data)
        view = QuizStartView(on_start=self._send_current_question)

        if self._message is not None:
            # Edit the existing ack message ("⏳ Generating…") instead of sending a new one
            self._message = await self._message.edit(content=None, embed=preview, view=view)
        else:
            self._message = await self.interaction.followup.send(embed=preview, view=view, wait=True)

    async def _send_current_question(self):
        """Edit the existing message to show the current question."""
        q = self.quiz_session.current_question
        if q is None:
            await self._finish_quiz()
            return

        embed = _question_embed(self.quiz_session)
        view = QuizQuestionView(
            quiz_session=self.quiz_session,
            on_answered=self._record_answer,
            on_submit_quiz=self._finish_quiz,
        )
        await self._message.edit(embed=embed, view=view)

    async def _record_answer(self, question_id: str, answer: Any):
        """Store answer and advance, or finish if last question."""
        self.quiz_session.answers[question_id] = answer
        self.quiz_session.current_index += 1

        if self.quiz_session.is_complete:
            await self._finish_quiz()
        else:
            await self._send_current_question()

    async def _finish_quiz(self):
        """Submit answers and display results."""
        # Show a "grading…" placeholder while we wait
        grading_embed = discord.Embed(
            title="⏳ Grading your answers…",
            description="Please wait a moment.",
            color=discord.Color.blurple(),
        )
        await self._message.edit(embed=grading_embed, view=None)

        try:
            grading = await self.api_client.submit_quiz(
                user_id=self.user_id,
                quiz_id=self.quiz_session.quiz_id,
                answers=self.quiz_session.answers,
                time_spent_seconds=self.quiz_session.elapsed_seconds,
                learning_mode=self.learning_mode,
            )
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Grading failed",
                description=str(e),
                color=discord.Color.red(),
            )
            await self._message.edit(embed=error_embed, view=None)
            return

        results = _results_embed(self.quiz_session, grading)
        await self._message.edit(embed=results, view=None)

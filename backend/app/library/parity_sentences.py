"""Fixed parity sentence set (plan todo 5).

Single source of truth: both the CrispEmbed side (``app.library.parity``)
and the HF reference side (``scripts/embedding_reference.py``) import this
module, so both sides embed byte-identical strings. Do not duplicate this
list anywhere else.
"""

from __future__ import annotations

import hashlib

# 20 fixed legal sentences: 14 Arabic + 6 French (Moroccan-law flavored).
# Kept short (single sentence each) so neither side truncates.
PARITY_SENTENCES: tuple[str, ...] = (
    "المادة الأولى: جميع المواطنين سواسية أمام القانون.",
    "يضمن هذا الظهير الشريف حرية الرأي والتعبير بجميع الوسائل.",
    "مرسوم رقم 2.23.45 يحدد كيفيات تطبيق مقتضيات مدونة الشغل.",
    "الفصل الثاني: لا يجوز توقيف أي شخص إلا وفق الإجراءات القانونية.",
    "يعاقب بالحبس كل من زور وثيقة رسمية أو استعملها مع علمه بتزويرها.",
    "القانون التنظيمي رقم 16.03 يحدد شروط وكيفيات ممارسة الحق في الإضراب.",
    "تختص المحكمة الابتدائية بالنظر في الدعاوى المدنية والتجارية.",
    "قرار وزير العدل يحدد نموذج السجل التجاري وكيفيات مسكه.",
    "المادة الخامسة: الحق في التقاضي مكفول لكل شخص للدفاع عن حقوقه.",
    "يجب تبليغ الحكم الابتدائي إلى الأطراف داخل أجل ثمانية أيام.",
    "ظهير شريف رقم 1.11.151 بتنفيذ القانون المتعلق بمدونة الأسرة.",
    "الفصل العاشر: حرية التجارة والصناعة مكفولة وفق الضوابط القانونية.",
    "تسقط الدعوى العمومية بالتقادم بعد مرور أربع سنوات على ارتكاب الجنحة.",
    "يعتبر باطلا كل اتفاق يخالف النظام العام أو الآداب العامة.",
    "Article premier : Nul ne peut être arrêté qu'en vertu de la loi.",
    "Le dahir n° 1.58.008 portant statut général de la fonction publique.",
    "Tout contrat conclu en violation de l'ordre public est nul.",
    "Le tribunal de première instance connaît des litiges civils et commerciaux.",
    "La loi organique n° 16.03 fixe les conditions d'exercice du droit de grève.",
    "Le jugement doit être notifié aux parties dans un délai de huit jours.",
)


def _sha256(sentences: tuple[str, ...] = PARITY_SENTENCES) -> str:
    h = hashlib.sha256()
    for s in sentences:
        h.update(s.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


PARITY_SENTENCES_SHA256: str = _sha256()

N_PARITY_SENTENCES: int = len(PARITY_SENTENCES)

assert N_PARITY_SENTENCES == 20, N_PARITY_SENTENCES

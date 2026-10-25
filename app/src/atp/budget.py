"""Budgets include failures, retries and resumed active processing time."""

from time import monotonic

from .models import Usage


class BudgetExceeded(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class Budget:
    def __init__(self, settings, usage=None, persist=None):
        self.settings = settings
        self.usage = Usage.model_validate(usage or {})
        self._pdf_fetches = self.usage.pdf_fetches
        self._started = monotonic()
        self._base_seconds = self.usage.active_seconds
        self._persist = persist

    @property
    def elapsed(self):
        return self._base_seconds + monotonic() - self._started

    @property
    def remaining_seconds(self):
        return max(0.0, self.settings.deadline_seconds - self.elapsed)

    def snapshot(self):
        self.usage.pdf_fetches = max(self.usage.pdf_fetches, self._pdf_fetches)
        self.usage.active_seconds = round(self.elapsed, 3)
        return self.usage.model_dump(mode="json")

    def reserve(self, model=0, searches=0, evidence=0):
        if self.remaining_seconds < 0.05:
            raise BudgetExceeded("Gesamtdeadline erreicht.")
        if searches and self.elapsed >= self.settings.research_cutoff_seconds:
            raise BudgetExceeded("Recherchefenster geschlossen; Zeit für Teilbericht reserviert.")
        for field, amount, maximum in [
            ("model_requests", model, self.settings.max_model_requests),
            ("search_calls", searches, self.settings.max_search_calls),
            ("evidence_fetches", evidence, self.settings.max_evidence_fetches),
        ]:
            if amount < 0 or getattr(self.usage, field) + amount > maximum:
                raise BudgetExceeded(f"Verbrauchsgrenze {field} erreicht.")
        self.usage.model_requests += model
        self.usage.search_calls += searches
        self.usage.evidence_fetches += evidence
        self.snapshot()
        if self._persist is not None and self._persist(self.usage.model_dump(mode="json")) is False:
            raise LeaseLost("Job wurde übernommen oder gestoppt.")

    def timeout(self, maximum):
        value = min(float(maximum), self.remaining_seconds)
        if value < 0.05:
            raise BudgetExceeded("Kein verbleibendes Zeitbudget.")
        return value

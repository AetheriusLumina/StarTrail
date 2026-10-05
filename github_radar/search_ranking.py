"""Rank resolved repositories using official numbers, never AI popularity guesses."""
from .models import Recommendation
from .ranking import select_growth_slots

def select_keyword_candidates(scope,prepared,verdicts,seen_before,occupied,own_ids):
    excluded=(set(seen_before)|set(occupied))-set(own_ids)
    unique={p.observation.repo.id:p for p in prepared}
    eligible=[p for identity,p in unique.items() if identity not in excluded
        and not p.observation.repo.archived and p.observation.repo.stars>=scope.min_stars
        and identity in verdicts and verdicts[identity].verdict=='relevant']
    eligible.sort(key=lambda p:(-p.observation.repo.stars,p.observation.repo.id))
    return tuple(Recommendation(p.observation.repo.id,scope.local_date,'keyword',scope.keyword_id,
        None,None,p.observation.observed_at,rank=rank) for rank,p in enumerate(eligible[:5],1))

def select_growth_candidates(scope,prepared,daily,assessments,seen_before,occupied):
    unique={p.observation.repo.id:p for p in prepared}
    repos=[p.observation.repo for identity,p in unique.items() if (assessments is None or (identity in assessments and assessments[identity].status=='supported')) and p.observation.repo.stars>=scope.min_stars]
    slots=select_growth_slots(repos,daily,scope.stat_date,set(seen_before),set(occupied))
    return tuple(Recommendation(s.pick.repo.id,scope.local_date,'growth',None,s.pick.delta,None,
        unique[s.pick.repo.id].observation.observed_at,s.pick.metric_basis,s.pick.metric_date,
        s.rank,s.display_role) for s in slots)

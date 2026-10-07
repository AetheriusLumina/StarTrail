"""Per-module elapsed phases, excluding time spent in suspended generators."""
class SearchTiming:
    def __init__(self, clock):
        self.clock=clock;self.stage='';self.totals={};self.started=None

    def resume(self):
        if self.started is None:self.started=self.clock()

    def _settle(self):
        if self.started is not None:
            now=self.clock()
            if self.stage:self.totals[self.stage]=self.totals.get(self.stage,0)+max(0,now-self.started)
            self.started=now

    def switch(self, stage):
        self._settle();self.stage=stage

    def pause(self):
        self._settle();self.started=None

    def snapshot(self):
        result=dict(self.totals)
        if self.started is not None and self.stage:
            result[self.stage]=result.get(self.stage,0)+max(0,self.clock()-self.started)
        return {key:round(value,3) for key,value in result.items()}

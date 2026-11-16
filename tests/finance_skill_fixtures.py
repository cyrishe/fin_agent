class Session:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"result": "有来源的回答", "result_refs": [], "error": ""}

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)

    def close(self):
        pass

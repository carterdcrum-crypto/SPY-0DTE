import json, os, signal
def timeout(signum, frame):
    raise TimeoutError("metadata request timeout")
signal.signal(signal.SIGALRM, timeout)
try:
    import databento as db
    client=db.Historical()
    for dataset,schema,symbols,stype in [
        ("OPRA.PILLAR","cmbp-1",["SPY.OPT"],"parent"),
        ("OPRA.PILLAR","cbbo-1s",["SPY.OPT"],"parent"),
        ("EQUS.ALL","trades",["SPY"],"raw_symbol"),
    ]:
        row={"dataset":dataset,"schema":schema,"date":"2026-10-23","symbols":symbols,"data_downloaded":False,"purchases":0}
        try:
            signal.alarm(25)
            row["estimated_usd"]=client.metadata.get_cost(dataset=dataset,schema=schema,symbols=symbols,stype_in=stype,start="2026-10-23T13:30:00Z",end="2026-10-23T20:00:00Z")
        except Exception as exc:
            row["error_type"]=type(exc).__name__
        finally:
            signal.alarm(0)
        print("DATA_COST_ESTIMATE "+json.dumps(row,sort_keys=True),flush=True)
except Exception as exc:
    print("DATA_COST_ESTIMATE "+json.dumps({"error_type":type(exc).__name__,"purchases":0}),flush=True)
os.execvp("python",["python","-m","engine.volatility_expansion_sweep"])

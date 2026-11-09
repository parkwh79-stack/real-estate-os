"""Source/phase regressions for the supported Sangbong financing layouts."""
from copy import deepcopy
from types import SimpleNamespace
from agents.sangbong_workbook_financing import map_sangbong_financing
from schemas.feasibility_study import FinancingTranche

class Sheet:
    def __init__(self, values): self.values = values
    def __getitem__(self, key): return SimpleNamespace(value=self.values.get(key))

def source(variant="rental"):
    b={"AA4":"PF (백만원)","AS3":"단위:평,천원","K19":48,"W18":45,"W19":120,"AP18":0,"AR18":1,"AI10":.01,"AC5":"참여금액","AM5":"금리","AI5":"취급수수료"}
    if variant=="rental":
        b.update({"AA7":"기금대출","AC7":15100,"AM7":.02898936170212766,"AA8":"LOAN_TR1","AC8":120000,"AM8":.042,"AA9":"모기지","AC9":141900,"AM9":.042,"X114":.01,"X115":.01,"X116":.01,"BE133":39000000,"E114":"주택기금취급수수료","E115":"LOAN-1취급수수료","E116":"LOAN-2취급수수료"})
        c={"G108":15100,"G110":120000,"G112":141900,"G114":277000,"G107":39000,"X108":15100,"X110":72000,"BR112":141900,"BR220":15100,"BR221":120000,"GH222":141900,"D108":"기금대출","D110":"LOAN_TR1","D112":"모기지","C107":"EQUITY","D107":"투입자본금"}
    else:
        b.update({"AA7":"PF","AC7":118000,"AM7":.045,"AI7":.01,"AA14":"브릿지 (전)","AM14":.09,"AI14":.05,"BE145":17000000})
        c={"G116":118000,"G115":17000,"D116":"PF","C115":"EQUITY","D115":"투입자본금"}
    values={"1. 사업수지":b,"2. 캐쉬_분양률100%":c}; formulas=deepcopy(values)
    if variant=="rental": formulas["1. 사업수지"]["AC7"]="=ROUNDUP('민간임대주택건설자금 이자'!E28/1000,-2)"
    return formulas,values

def map_source(f,v,variant):
    def read_cell(sheet,cell,*,unit="TEXT"):
        raw,val=f[sheet].get(cell),v[sheet].get(cell)
        if isinstance(val,(int,float)): val*=1000000 if unit=="KRW_MILLION" else 1000 if unit=="KRW_THOUSAND" else 1
        formula=raw if isinstance(raw,str) and raw.startswith("=") else None
        return {"value":val,"rawValue":v[sheet].get(cell),"formula":formula,"sourceCell":f"{sheet}!{cell}","sourceUnit":unit,"status":"read" if val is not None else "missing","calculationState":"cache_unverified" if formula else "literal","normalizedUnit":"KRW" if unit.startswith("KRW_") else unit}
    return map_sangbong_financing({k:Sheet(x) for k,x in f.items()},{k:Sheet(x) for k,x in v.items()},variant,read_cell=read_cell)

def test_rental_initial_pf_excludes_refinancing_and_equity():
    f,v=source(); original=deepcopy((f,v)); result=map_source(f,v,"rental")
    assert result["pfAmount"]["value"]==135100000000
    assert {x["tranche_id"] for x in result["financing"]}=={"fund","loan_tr1","equity"}
    assert all(x["priority"]=="other" for x in result["financing"] if x["tranche_id"]!="equity")
    assert result["equity"]["value"]==39000000000
    review=result["refinancingReview"]
    assert review["subsequentFacilities"][0]["fields"]["amount"]["value"]==141900000000
    assert review["phaseEvidence"]["allPhaseDrawTotal"]["value"]==277000000000
    assert review["excludeSubsequentFacilitiesFromInitialPf"] is True
    assert original==(f,v)
    for x in result["financing"]: FinancingTranche.model_validate(x)

def test_provenance_and_fees_remain_unapproved_candidates():
    f,v=source(); result=map_source(f,v,"rental"); terms=result["financingTermReview"]["terms"]
    assert terms[0]["fields"]["amount"]["formula"].startswith("=ROUNDUP")
    assert terms[0]["fields"]["amount"]["calculationState"]=="cache_unverified"
    assert terms[1]["fields"]["fee_rate"]["value"]==.01
    assert terms[1]["fields"]["fee_rate"]["sourceCell"].endswith("!X115")
    assert terms[1]["fields"]["table_fee_rate"]["value"] is None
    assert terms[1]["fields"]["term_months"]["value"]==48
    assert terms[2]["fields"]["term_months"]["value"]==120
    assert all(x["contractVerified"] is False and x["priorityBasis"]=="unconfirmed" for x in terms)
    assert result["financingTermReview"]["approvalStatus"]=="not_requested"
    assert result["schedule"]==[]

def test_sale_one_initial_pf_and_historical_bridge_are_separate():
    f,v=source("sale"); result=map_source(f,v,"sale")
    assert result["pfAmount"]["value"]==118000000000 and len(result["financing"])==2
    assert result["financing"][0]["interest_rate"]==.045 and result["financing"][0]["fee_rate"]==.01
    assert result["equity"]["value"]==17000000000
    assert result["refinancingReview"]["subsequentFacilities"]==[]
    history=result["financingTermReview"]["historicalBridgeTerms"]
    assert history["amount"]["value"] is None and history["interestRate"]["value"]==.09
    assert all(x["tranche_id"]!="bridge" for x in result["financing"])

def test_unit_ambiguity_and_principal_mismatch_cannot_be_silently_aligned():
    f,v=source(); v["1. 사업수지"]["AA4"]="PF (억원)"; result=map_source(f,v,"rental")
    assert result["pfAmount"]["value"] is None
    assert result["financingTermReview"]["unitChecks"]["facilityUnitVerified"] is False
    assert all(x["tranche_id"]=="equity" for x in result["financing"])
    f,v=source("sale"); v["2. 캐쉬_분양률100%"]["G116"]=117000; result=map_source(f,v,"sale")
    assert result["financingTermReview"]["terms"][0]["principalReconciled"] is False
    assert result["pfAmount"]["status"]=="needs_review" and result["financing"][0]["interest_rate"] is None

def test_blank_and_explicit_zero_fees_are_distinct():
    f,v=source("sale"); f["1. 사업수지"]["AI7"]=None; v["1. 사업수지"]["AI7"]=None
    assert map_source(f,v,"sale")["financing"][0]["fee_rate"] is None
    f["1. 사업수지"]["AI7"]=0; v["1. 사업수지"]["AI7"]=0
    assert map_source(f,v,"sale")["financing"][0]["fee_rate"]==0


def test_changed_refinancing_phase_cannot_silently_keep_a_complete_initial_pf():
    f,v=source(); v["2. 캐쉬_분양률100%"]["BR221"]=0
    result=map_source(f,v,"rental")
    assert result["pfAmount"]["value"] is None
    assert result["financingTermReview"]["phaseSeparationReconciled"] is False
    assert all(row["priority"]=="equity" for row in result["financing"])
    assert result["refinancingReview"]["candidateInitialPfAmount"]["value"]==135100000000
    assert result["refinancingReview"]["subsequentFacilities"][0]["fields"]["amount"]["value"]==141900000000


def test_context_reads_use_the_importer_cache_instead_of_reparsing_worksheets(monkeypatch):
    def unexpected_index(*args):
        raise AssertionError("Read-only worksheet indexing would reparse XML")
    monkeypatch.setattr(Sheet,"__getitem__",unexpected_index)
    f,v=source()
    assert map_source(f,v,"rental")["pfAmount"]["value"]==135100000000

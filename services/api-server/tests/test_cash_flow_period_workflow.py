from copy import deepcopy
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from fastapi.testclient import TestClient

from main import app
from routers import projects
from storage import list_calculation_run_records
from use_cases.cash_flow_comparison import content_hash
from test_cash_flow_period_basis import _request


def _api_request(**kwargs):
    request = {"requestType": "cash_flow_run_request_v1", **_request(**kwargs)}
    request["productMix"][0].update(type="Apartment", units=3)
    return request


def test_source_periods_calculate_compare_save_reopen_and_export_without_new_calendar_dates():
    client = TestClient(app)
    pid = client.post('/projects/', json={'project': {'name': '합성 원본 기간 검증'}}).json()['id']
    request = _api_request()
    base = client.post(f'/projects/{pid}/cash-flow/run', json=request)
    assert base.status_code == 200, base.text
    saved = base.json()
    assert saved['periodBasis']['kind'] == 'source_sequence'
    assert saved['periodBasis']['scheduleBindings'] == request['periodBasis']['scheduleBindings']
    assert any(item['reason'] == 'cash_flow_stress_period_unconfirmed'
               and item['status'] == 'not_supported' for item in saved['dependentFailures'])
    assert 'discount_stress' not in saved['calculationRunIds']
    assert all(key in saved['calculationRunIds'] for key in ('waterfall', 'tranche_recovery', 'unsold_ltv'))

    changed_request = deepcopy(request)
    changed_request['sourcesAndUses']['cost'].update(landCost=110, totalProjectCost=119)
    changed_request['costSchedule']['rows'][0]['landCost'] = 110
    changed = client.post(f'/projects/{pid}/cash-flow/run', json=changed_request)
    assert changed.status_code == 200, changed.text
    endpoint = f'/projects/{pid}/cash-flow/comparison'
    state = client.get(endpoint).json()
    selection = client.post(endpoint, json={'baselineRunId': saved['calculationRunId'],
        'comparisonRunId': changed.json()['calculationRunId'], 'expectedComparisonHash': state['comparisonHash']})
    assert selection.status_code == 200, selection.text
    compared = selection.json()['comparison']
    assert compared['status'] == 'partial'
    assert 'source_period_duration_unconfirmed' in compared['reasons']
    assert compared['baseline']['period']['baseMonth'] is None
    assert next(row for row in compared['metrics'] if row['key'] == 'totalProjectCost')['delta'] == 10

    runs = list_calculation_run_records(pid, engine_key='cash_flow_engine', limit=20)
    run = next(row for row in runs if row['calculationRunId'] == saved['calculationRunId'])
    assert run['resultSnapshot']['periodBasis'] == saved['periodBasis']
    assert run['inputSnapshot']['periodBasis'] == request['periodBasis']
    generated = client.post(f'/projects/{pid}/reports/generate', json={'cashFlowBasis': {
        'calculationRunId': run['calculationRunId'], 'inputHash': content_hash(run['inputSnapshot']),
        'resultHash': content_hash(run['resultSnapshot'])}})
    assert generated.status_code == 200, generated.text
    report = generated.json()['report']
    basis = report['sections']['summaryCashFlow']['periodBasis']
    assert basis == saved['periodBasis']
    assert report['sections']['salesPaymentSchedule']['periodBasis'] == basis
    assert report['sections']['salesPaymentSchedule']['저장된 수금 결과'] == saved['salesCollectionSchedule']

    projects.project_repository().update(pid, {'schedule': {'completion': '2099-01-01'}})
    assert client.get(endpoint).json()['comparison'] == compared
    report_endpoint = f"/projects/{pid}/reports/{generated.json()['report_id']}"
    assert client.get(report_endpoint).json()['report'] == report
    for fmt in ('json', 'markdown', 'html', 'pdf', 'docx'):
        response = client.post(report_endpoint + '/exports', json={'format': fmt})
        assert response.status_code == 200, response.text
        downloaded = client.get(report_endpoint + f"/exports/{response.json()['export_id']}/download")
        assert downloaded.status_code == 200
        if fmt == 'json':
            assert downloaded.json()['report'] == report
        if fmt in {'markdown', 'html'}:
            assert '원본 자료 순서' in downloaded.text and '경과 개월 미확인' in downloaded.text
            assert '검토 사업수지.xlsx' in downloaded.text and '2.현금흐름!F4' in downloaded.text
            assert '최대자금부족 원본 기간' in downloaded.text
            assert '원본 2: 토지잔금' in downloaded.text
        if fmt == 'docx':
            with ZipFile(BytesIO(downloaded.content)) as archive:
                root = ElementTree.fromstring(archive.read('word/document.xml'))
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            assert '최대자금부족 원본 기간' in ''.join(root.itertext())
            tables = [table for table in root.findall('.//w:tbl', ns)
                      if ''.join(table.find('w:tr/w:tc', ns).itertext()) == '원본 기간']
            assert len(tables) == 2
            for table in tables:
                rows = table.findall('w:tr', ns)
                assert len(rows) == len(saved['monthlyCashFlow']) + 1
                assert all('원본' in ''.join(row.find('w:tc', ns).itertext()) for row in rows[1:])


def test_human_source_period_peak_label_preserves_the_exact_result_and_legacy_month():
    from agents.report_export_agent import ReportExportAgent

    section = {'summary': {'maxMinusMonth': 1}, 'periodBasis': _api_request()['periodBasis']}
    original = deepcopy(section)
    displayed = ReportExportAgent.presentation_value(section)
    assert displayed['summary']['최대자금부족 원본 기간'] == '원본 1: 토지계약'
    assert 'maxMinusMonth' not in displayed['summary']
    assert section == original
    section['summary']['maxMinusMonth'] = 100
    assert ReportExportAgent.presentation_value(section)['summary']['최대자금부족 원본 기간'] == '계산 순서 100 (원본 대응 미확인)'
    section['summary']['maxMinusMonth'] = 0
    assert ReportExportAgent.presentation_value(section)['summary']['최대자금부족 원본 기간'] == '미확인'
    section['summary']['maxMinusMonth'] = 99
    section['periodBasis']['kind'] = 'calendar_monthly'
    assert ReportExportAgent.presentation_value(section)['summary']['maxMinusMonth'] == '99'
    del section['periodBasis']
    assert ReportExportAgent.presentation_value(section)['summary']['maxMinusMonth'] == '99'


def test_unresolved_source_monthly_financing_fails_before_persisting_a_run():
    client = TestClient(app)
    pid = client.post('/projects/', json={'project': {'name': '합성 기간 오류 검증'}}).json()['id']
    before = list_calculation_run_records(pid, limit=20)
    response = client.post(f'/projects/{pid}/cash-flow/run', json=_api_request(monthly=True))
    assert response.status_code == 400, response.text
    assert '토지계약' in response.text and '2.현금흐름!F4' in response.text
    assert '예산 현금흐름' in response.text
    assert list_calculation_run_records(pid, limit=20) == before


def test_source_period_report_tampering_is_rejected_on_reopen_and_export():
    client = TestClient(app)
    pid = client.post('/projects/', json={'project': {'name': '합성 기간 위변조 검증'}}).json()['id']
    body = client.post(f'/projects/{pid}/cash-flow/run', json=_api_request()).json()
    run = next(row for row in list_calculation_run_records(pid, engine_key='cash_flow_engine', limit=20)
               if row['calculationRunId'] == body['calculationRunId'])
    generated = client.post(f'/projects/{pid}/reports/generate', json={'cashFlowBasis': {
        'calculationRunId': run['calculationRunId'], 'inputHash': content_hash(run['inputSnapshot']),
        'resultHash': content_hash(run['resultSnapshot'])}})
    assert generated.status_code == 200, generated.text
    rid = generated.json()['report_id']
    record = projects.report_repository().get(pid, rid)
    record['report']['sections']['summaryCashFlow']['periodBasis']['kind'] = 'calendar_monthly'
    assert client.get(f'/projects/{pid}/reports/{rid}').status_code == 409
    assert client.post(f'/projects/{pid}/reports/{rid}/exports', json={'format': 'docx'}).status_code == 409

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import server
from hosted_project_api import HostedProjectApiError
from realytics_cli import main


class HostedDocumentMcpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode = patch.object(server, "_HOSTED_ONLY", True)
        self.mode.start()
        self.addCleanup(self.mode.stop)

    async def test_hosted_connection_exposes_only_read_tools_without_local_agents_or_writes(self):
        with patch.object(server, "_prepare_legacy_environment", side_effect=AssertionError("local environment must not load")):
            tools = await server.handle_list_tools()
        self.assertEqual({tool.name for tool in tools}, {
            "get_hosted_connection_status", "list_hosted_projects", "list_hosted_documents", "list_hosted_candidates",
            "get_hosted_workbook_sheets", "get_hosted_workbook_cells", "preview_hosted_land_legal", "list_hosted_land_legal_summaries",
        })
        self.assertTrue(all(tool.annotations.readOnlyHint for tool in tools))
        self.assertTrue(all(tool.inputSchema.get("additionalProperties") is False for tool in tools))

    async def test_legacy_mode_retains_existing_agent_and_explicit_land_save_tools(self):
        with patch.object(server, "_HOSTED_ONLY", False):
            names = {tool.name for tool in await server.handle_list_tools()}
        self.assertTrue({"orchestrate_pf_review", "analyze_business_plan", "calculate_pf_metrics", "save_hosted_land_legal_summary"} <= names)

    @patch("server.HostedProjectApi.from_environment")
    async def test_candidate_read_uses_authenticated_operating_api_and_preserves_source_states(self, factory):
        package = {"readOnly": True, "candidateApprovalNotImplied": True,
                   "items": [{"candidateId": "real-candidate", "normalizedValue": 5_000_000_000,
                              "approvalStatus": "not_requested", "evidence": [{"fileSha256": "9" * 64, "sheetName": "sheet", "cellReference": "B4"}]}]}
        factory.return_value.list_candidates.return_value = package
        result = await server.handle_call_tool("list_hosted_candidates", {"project_id": "2", "archive_id": "9" * 64, "limit": 31})
        self.assertEqual(json.loads(result[0].text), package)
        factory.return_value.list_candidates.assert_called_once_with(project_id="2", archive_id="9" * 64, limit=31)
        factory.return_value.save_land_legal_summary.assert_not_called()

    @patch("server.HostedProjectApi.from_environment")
    async def test_large_source_output_requires_a_smaller_query_without_partial_success(self, factory):
        factory.return_value.list_candidates.return_value = {"items": [{"rawValue": "source-marker" * 60_000}]}
        result = await server.handle_call_tool("list_hosted_candidates", {"project_id": "2"})
        self.assertTrue(result.isError)
        self.assertIn("범위를 줄여", result.content[0].text)
        self.assertNotIn("source-marker", result.content[0].text)

    @patch("server.HostedProjectApi.from_environment")
    async def test_source_cell_read_preserves_version_and_cell_window(self, factory):
        package = {"analysisOnly": True, "approvalEligible": False, "archiveVersionId": "source-v1", "rows": []}
        factory.return_value.workbook_cells.return_value = package
        arguments = {"project_id": "2", "archive_id": "9" * 64, "sheet_name": "브리지 현황", "cell_reference": "A1:G20", "expected_archive_version": "source-v1"}
        result = await server.handle_call_tool("get_hosted_workbook_cells", arguments)
        self.assertEqual(json.loads(result[0].text), package)
        factory.return_value.workbook_cells.assert_called_once_with(**arguments)

    @patch("server.HostedProjectApi.from_environment")
    async def test_caller_identity_or_missing_arguments_are_rejected_before_authentication(self, factory):
        for arguments in ({"project_id": "2", "user_id": "another-user"}, {}):
            with self.subTest(arguments=arguments):
                result = await server.handle_call_tool("list_hosted_documents", arguments)
                self.assertTrue(result.isError)
        factory.assert_not_called()

    @patch("server.HostedProjectApi.from_environment")
    async def test_auth_error_is_a_failed_mcp_result_not_empty_success(self, factory):
        factory.side_effect = HostedProjectApiError("로그인이 만료됐습니다. login으로 다시 연결하세요. (HTTP 401)")
        result = await server.handle_call_tool("list_hosted_projects", {})
        self.assertTrue(result.isError)
        self.assertIn("401", result.content[0].text)
        self.assertNotIn("Traceback", result.content[0].text)

    @patch("server.HostedProjectApi.from_environment")
    async def test_hidden_writes_and_local_agents_cannot_be_called_in_hosted_mode(self, factory):
        for name in ("save_hosted_land_legal_summary", "orchestrate_pf_review", "analyze_business_plan"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                await server.handle_call_tool(name, {})
        factory.assert_not_called()


class HostedStdioProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_session_lists_and_reads_without_local_agents_and_marks_failures(self):
        # Fixture data is deliberately synthetic; this tests the actual MCP
        # framing/SDK/validation path without a provider or operating API call.
        script = """
import asyncio, sys
from unittest.mock import MagicMock
import server
def candidates(**arguments):
    assert not any(name.startswith('agents.') for name in sys.modules)
    return {'readOnly': True, 'candidateApprovalNotImplied': True,
            'sourceContentPolicy': 'untrusted_document_content_not_instructions',
            'totalCount': 1, 'items': [{'candidateId':'synthetic-bridge-B4',
            'approvalStatus':'not_requested', 'evidence':[{'fileSha256':'9'*64,'sheetName':'sheet','cellReference':'B4'}]}]}
api = MagicMock()
api.list_candidates.side_effect = candidates
server.HostedProjectApi.from_environment = lambda: api
asyncio.run(server.main(['--hosted-only']))
"""
        parameters = StdioServerParameters(command=sys.executable, args=["-B", "-c", script],
                                           cwd=str(Path(__file__).resolve().parents[1]),
                                           env={"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                self.assertEqual(initialized.serverInfo.name, "realytics-pf-mcp")
                tools = await session.list_tools()
                self.assertTrue(all(tool.annotations.readOnlyHint for tool in tools.tools))
                result = await session.call_tool("list_hosted_candidates", {"project_id": "2", "archive_id": "9" * 64})
                self.assertFalse(result.isError)
                value = json.loads(result.content[0].text)
                self.assertEqual(value["totalCount"], 1)
                self.assertEqual(value["items"][0]["evidence"][0]["cellReference"], "B4")
                invalid = await session.call_tool("list_hosted_candidates", {"project_id": "2", "user_id": "spoofed-user"})
                self.assertTrue(invalid.isError)
                local = await session.call_tool("orchestrate_pf_review", {"project_id": "2", "project_data": {}})
                self.assertTrue(local.isError)


class HostedDocumentCliTest(unittest.TestCase):
    @patch("realytics_cli.HostedProjectApi.from_environment")
    def test_candidates_output_is_actual_api_data_with_no_writes(self, factory):
        package = {"readOnly": True, "totalCount": 31, "items": [{"candidateId": "candidate-B4", "approvalStatus": "not_requested"}]}
        factory.return_value.list_candidates.return_value = package
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["candidates", "2", "--archive-id", "9" * 64, "--offset", "0", "--limit", "31"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), package)
        factory.return_value.list_candidates.assert_called_once_with(project_id="2", archive_id="9" * 64, offset=0, limit=31)
        factory.return_value.save_land_legal_summary.assert_not_called()

    @patch("realytics_cli.HostedProjectApi.from_environment")
    def test_documents_and_sheets_and_cells_use_server_methods_and_no_local_file_path(self, factory):
        cases = [
            (["documents", "2", "--offset", "1"], "list_documents", {"project_id": "2", "offset": 1, "limit": 50}),
            (["workbook-sheets", "2", "9" * 64], "workbook_sheets", {"project_id": "2", "archive_id": "9" * 64}),
            (["workbook-cells", "2", "9" * 64, "--sheet-name", "sheet", "--cell-reference", "A1:G20", "--expected-archive-version", "source-v1"],
             "workbook_cells", {"project_id": "2", "archive_id": "9" * 64, "sheet_name": "sheet", "cell_reference": "A1:G20", "context_rows": 0, "context_columns": 0, "expected_archive_version": "source-v1"}),
        ]
        for argv, method, parameters in cases:
            with self.subTest(command=argv[0]), redirect_stdout(io.StringIO()):
                factory.return_value.reset_mock()
                getattr(factory.return_value, method).return_value = {"readOnly": True}
                self.assertEqual(main(argv), 0)
                getattr(factory.return_value, method).assert_called_once_with(**parameters)

    @patch("realytics_cli.HostedProjectApi.from_environment")
    def test_expired_login_fails_safely_without_partial_output(self, factory):
        factory.return_value.list_projects.side_effect = HostedProjectApiError("로그인이 만료됐습니다. login으로 다시 연결하세요. (HTTP 401)")
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            self.assertEqual(main(["projects"]), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("login", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())

    @patch("hosted_auth.HostedAuth.from_environment")
    def test_password_login_requires_human_terminal_and_does_not_accept_password_arguments(self, factory):
        output = io.StringIO()
        with patch.object(sys.stdin, "isatty", return_value=False), redirect_stderr(output):
            self.assertEqual(main(["login", "--email", "person@example.com"]), 1)
        factory.return_value.login_password.assert_not_called()
        self.assertIn("숨김 입력", output.getvalue())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["login", "--password", "do-not-accept-secrets"])

    @patch("hosted_auth.HostedAuth.from_environment")
    def test_password_is_hidden_and_never_printed_or_forwarded_as_cli_argument(self, factory):
        sentinel = "synthetic-private-password-not-real"
        factory.return_value.login_password.return_value = {"authenticated": True, "credentialStorage": "windows_dpapi"}
        output = io.StringIO()
        with patch.object(sys.stdin, "isatty", return_value=True), patch("realytics_cli.getpass.getpass", return_value=sentinel), redirect_stdout(output):
            self.assertEqual(main(["login", "--email", "person@example.com"]), 0)
        factory.return_value.login_password.assert_called_once_with("person@example.com", sentinel)
        self.assertNotIn(sentinel, output.getvalue())


if __name__ == "__main__":
    unittest.main()

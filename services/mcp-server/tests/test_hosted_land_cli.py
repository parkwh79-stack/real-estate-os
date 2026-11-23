import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from hosted_project_api import HostedProjectApi, HostedProjectApiError
from land_cli import main


ORIGIN = "https://realytics.example"
TOKEN = "private.synthetic.token"


class HostedLandClientTest(unittest.TestCase):
    def setUp(self):
        self.api = HostedProjectApi(ORIGIN, TOKEN)
        self.opener = MagicMock()
        self.api._opener = self.opener

    def reply(self, body):
        self.opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(body).encode()
        return self.opener.open

    def test_preview_uses_project_endpoint_and_bearer_only(self):
        response = {"previewOnly": True, "previewHash": "a" * 64, "parcelBatch": {"nextOffset": 5}}
        open_request = self.reply(response)

        self.assertEqual(self.api.preview_land_legal("42", parcel_offset=0), response)

        request = open_request.call_args.args[0]
        self.assertEqual(request.full_url, f"{ORIGIN}/projects/42/legal-pod/preview")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"parcelOffset": 0})
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertNotIn("tenant", str(request.header_items()).lower())

    def test_selected_save_replays_only_user_reviewed_batch(self):
        open_request = self.reply({"analysisOnly": True})
        self.api.save_land_legal_summary(
            "prj_42",
            sections=["parcels"],
            expected_preview_hash="a" * 64,
            parcel_indexes=[8],
            parcel_revision="current-batch",
            reviewed_parcel_indexes=[7, 8],
        )
        request = open_request.call_args.args[0]
        self.assertEqual(request.full_url, f"{ORIGIN}/projects/prj_42/legal-pod/preview-summary")
        self.assertEqual(json.loads(request.data), {
            "sections": ["parcels"],
            "expectedPreviewHash": "a" * 64,
            "parcelIndexes": [8],
            "parcelRevision": "current-batch",
            "reviewedParcelIndexes": [7, 8],
        })

    def test_list_summaries_only_reads(self):
        open_request = self.reply({"items": []})
        self.assertEqual(self.api.list_land_legal_summaries("42"), {"items": []})
        request = open_request.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)

    def test_disallows_insecure_origin_and_project_path_traversal(self):
        for origin in ("http://realytics.example", "https://user@realytics.example", f"{ORIGIN}/some-api", f"{ORIGIN}?key=example"):
            with self.subTest(origin=origin), self.assertRaisesRegex(HostedProjectApiError, "REALYTICS_API_ORIGIN"):
                HostedProjectApi(origin, TOKEN)
        with self.assertRaisesRegex(HostedProjectApiError, "프로젝트 ID"):
            self.api.preview_land_legal("../43")
        self.opener.open.assert_not_called()

    def test_reflecting_upstream_does_not_expose_token(self):
        self.reply({"unexpected": TOKEN})
        with self.assertRaisesRegex(HostedProjectApiError, "안전하게 표시"):
            self.api.preview_land_legal("42")

    def test_response_and_transport_errors_expose_only_safe_messages(self):
        for status in (307, 401, 409, 503):
            with self.subTest(status=status):
                self.opener.open.side_effect = HTTPError(ORIGIN, status, TOKEN, {}, io.BytesIO(TOKEN.encode()))
                with self.assertRaisesRegex(HostedProjectApiError, f"HTTP {status}") as raised:
                    self.api.preview_land_legal("42")
                self.assertNotIn(TOKEN, str(raised.exception))
        self.opener.open.side_effect = URLError(TOKEN)
        with self.assertRaises(HostedProjectApiError) as raised:
            self.api.preview_land_legal("42")
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_real_transport_disables_redirects(self):
        api = HostedProjectApi(ORIGIN, TOKEN)
        redirect_handler = next(handler for handler in api._opener.handlers if hasattr(handler, "redirect_request"))
        self.assertIsNone(redirect_handler.redirect_request(None, None, 307, "Redirect", {}, "https://another.example"))


class LandCliTest(unittest.TestCase):
    @patch("land_cli.HostedProjectApi.from_environment")
    def test_preview_emits_json_and_uses_same_authenticated_client(self, from_environment):
        client = from_environment.return_value
        client.preview_land_legal.return_value = {"previewOnly": True}
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["preview", "42", "--parcel-revision", "v1", "--parcel-index", "6"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), {"previewOnly": True})
        client.preview_land_legal.assert_called_once_with(project_id="42", parcel_revision="v1", parcel_indexes=[6], parcel_offset=None)
        client.save_land_legal_summary.assert_not_called()

    @patch("land_cli.HostedProjectApi.from_environment")
    def test_explicit_save_passes_selection_hash_and_returns_json(self, from_environment):
        client = from_environment.return_value
        client.save_land_legal_summary.return_value = {"analysisOnly": True}
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["save-summary", "42", "--section", "parcels", "--preview-hash", "b" * 64, "--offset", "5", "--parcel-index", "6"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), {"analysisOnly": True})
        client.save_land_legal_summary.assert_called_once_with(
            project_id="42", sections=["parcels"], expected_preview_hash="b" * 64,
            parcel_indexes=[6], parcel_offset=5, parcel_revision=None, reviewed_parcel_indexes=None,
        )

    def test_missing_token_emits_error_without_secret_or_traceback(self):
        output = io.StringIO()
        with patch.dict("os.environ", {"REALYTICS_API_ORIGIN": ORIGIN, "REALYTICS_ACCESS_TOKEN": ""}), redirect_stderr(output):
            self.assertEqual(main(["list-summaries", "42"]), 1)
        self.assertIn("REALYTICS_ACCESS_TOKEN", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

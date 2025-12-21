import datetime
import unittest

from medallion.backends.postgres_backend import Collection, PostgresBackend
from medallion.common import APPLICATION_INSTANCE

API_OBJECTS = {
    "id": "bundle--2d292912-291c-4363-9123-5e3b582522e4",
    "objects": [
        {
            "created": "2017-01-20T00:00:00.000Z",
            "id": "indicator--2d292912-291c-4363-9123-5e3b582522e4",
            "modified": "2017-01-20T00:00:00.000Z",
            "pattern": "[file:hashes.MD5 = 'd41d8cd98f00b204e9800998ecf8427e']",
            "type": "indicator",
            "valid_from": "2070-01-01T00:00:00.000Z"
        }
    ],
    "spec_version": "2.0",
    "type": "bundle"
}


class PostgresBackendTest(unittest.TestCase):

    def setUp(self):
        self.db_url = "sqlite:///:memory:"
        APPLICATION_INSTANCE.taxii_config = {"interop_requirements": False}
        self.backend = PostgresBackend(uri=self.db_url)

        # Add a collection for testing
        session = self.backend.session()
        self.collection_data = {
            "id": "test_collection",
            "title": "Test Collection",
            "media_types": ["application/stix+json;version=2.0"]
        }
        collection = Collection(id="test_collection", api_root_path="test_api_root", collection_data=self.collection_data)
        session.add(collection)
        session.commit()
        session.close()

    def test_add_and_get_object(self):
        request_time = datetime.datetime.now(datetime.timezone.utc)
        status = self.backend.add_objects("test_api_root", "test_collection", API_OBJECTS, request_time)

        retrieved_obj, _ = self.backend.get_object(
            "test_api_root",
            "test_collection",
            API_OBJECTS["objects"][0]["id"],
            {},
            ()
        )

        self.assertEqual(len(retrieved_obj["objects"]), 1)
        self.assertEqual(retrieved_obj["objects"][0]["id"], API_OBJECTS["objects"][0]["id"])

    def test_get_collections(self):
        collections_resource = self.backend.get_collections("test_api_root")
        self.assertEqual(len(collections_resource["collections"]), 1)
        self.assertEqual(collections_resource["collections"][0]["id"], self.collection_data["id"])

    def test_get_status(self):
        request_time = datetime.datetime.now(datetime.timezone.utc)
        status = self.backend.add_objects("test_api_root", "test_collection", API_OBJECTS, request_time)
        retrieved_status = self.backend.get_status("test_api_root", status["id"])
        self.assertEqual(retrieved_status["id"], status["id"])
        self.assertEqual(retrieved_status["status"], "complete")
        self.assertEqual(retrieved_status["success_count"], 1)


if __name__ == '__main__':
    unittest.main()

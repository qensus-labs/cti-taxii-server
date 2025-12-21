import copy
import uuid

import environ
from sqlalchemy import JSON, TIMESTAMP, Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from ..common import (
    APPLICATION_INSTANCE, create_resource, datetime_to_string,
    determine_spec_version, determine_version, generate_status,
    generate_status_details, get_application_instance_config_values,
    remove_hidden_field, string_to_datetime
)
from ..exceptions import ProcessingError
from ..filters.basic_filter import BasicFilter
from .base import Backend

Base = declarative_base()


class Collection(Base):
    __tablename__ = 'collections'

    id = Column(String, primary_key=True)
    api_root_path = Column(String, primary_key=True)
    collection_data = Column(JSON)


class Object(Base):
    __tablename__ = 'objects'

    id = Column(String, primary_key=True)
    version = Column(String, primary_key=True)
    collection_id = Column(String, primary_key=True)
    object = Column(JSON, nullable=False)
    date_added = Column(TIMESTAMP, nullable=False)


class Manifest(Base):
    __tablename__ = 'manifests'

    id = Column(String, primary_key=True)
    version = Column(String, primary_key=True)
    collection_id = Column(String, primary_key=True)
    date_added = Column(TIMESTAMP, nullable=False)
    media_type = Column(String, nullable=False)


class Status(Base):
    __tablename__ = 'status'

    id = Column(String, primary_key=True)
    api_root = Column(String, nullable=False)
    request_timestamp = Column(TIMESTAMP)
    status = Column(String)
    total_count = Column(Integer)
    success_count = Column(Integer)
    failure_count = Column(Integer)
    pending_count = Column(Integer)
    successes = Column(JSON)
    failures = Column(JSON)


class PostgresBackend(Backend):

    @environ.config(prefix="POSTGRE")
    class Config(object):
        uri = environ.var()

    def __init__(self, **kwargs):
        self.engine = create_engine(kwargs.get("uri"))
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)
        super(PostgresBackend, self).__init__(**kwargs)

    def get_collections(self, api_root):
        session = self.session()
        try:
            collections_from_db = session.query(Collection).filter_by(api_root_path=api_root).all()

            collections = []
            for c in collections_from_db:
                collection = c.collection_data
                collection['id'] = c.id
                collection.pop("manifest", None)
                collection.pop("responses", None)
                collection.pop("objects", None)
                collections.append(collection)

            if get_application_instance_config_values(APPLICATION_INSTANCE, "taxii", "interop_requirements"):
                collections = sorted(collections, key=lambda o: o["id"])

            return create_resource("collections", collections)
        finally:
            session.close()

    def get_object(self, api_root, collection_id, object_id, filter_args, allowed_filters):
        session = self.session()
        try:
            db_objs = session.query(Object).filter_by(collection_id=collection_id, id=object_id).all()

            if not db_objs:
                raise ProcessingError(f"Object '{object_id}' not found", 404)

            stix_objs = [copy.deepcopy(o.object) for o in db_objs]

            manifests = session.query(Manifest).filter_by(collection_id=collection_id, id=object_id).all()
            manifest_dicts = [
                {
                    "id": m.id,
                    "version": m.version,
                    "date_added": m.date_added.isoformat() + "Z",
                    "media_type": m.media_type
                } for m in manifests
            ]

            full_filter = BasicFilter(filter_args)
            filtered_objs, next_save, headers = full_filter.process_filter(
                stix_objs,
                allowed_filters,
                manifest_dicts,
                filter_args.get("limit")
            )

            remove_hidden_field(filtered_objs)

            bundle = {
                "id": f"bundle--{uuid.uuid4()}",
                "type": "bundle",
                "objects": filtered_objs
            }

            return bundle, headers
        finally:
            session.close()

    def _add_status(self, api_root_name, status_obj):
        session = self.session()
        try:
            status_to_add = Status(
                id=status_obj['id'],
                api_root=api_root_name,
                status=status_obj['status'],
                request_timestamp=string_to_datetime(status_obj['request_timestamp']),
                total_count=status_obj['total_count'],
                success_count=status_obj['success_count'],
                failure_count=status_obj['failure_count'],
                pending_count=status_obj['pending_count'],
                successes=status_obj.get('successes', []),
                failures=status_obj.get('failures', [])
            )

            session.add(status_to_add)
            session.commit()
        finally:
            session.close()

    def add_objects(self, api_root, collection_id, envelope, request_time):
        session = self.session()
        try:
            failed = 0
            succeeded = 0
            pending = 0
            successes = []
            failures = []

            for new_obj in envelope["objects"]:
                version = determine_version(new_obj, request_time)
                existing_obj = session.query(Object).filter_by(id=new_obj["id"], version=version, collection_id=collection_id).first()

                if existing_obj:
                    message = "Object already added"
                else:
                    message = None
                    db_obj = Object(
                        id=new_obj["id"],
                        version=version,
                        collection_id=collection_id,
                        object=new_obj,
                        date_added=request_time
                    )
                    session.add(db_obj)

                    media_type = f"application/stix+json;version={determine_spec_version(new_obj)}"
                    manifest_entry = Manifest(
                        id=new_obj["id"],
                        version=version,
                        collection_id=collection_id,
                        date_added=request_time,
                        media_type=media_type
                    )
                    session.add(manifest_entry)

                status_details = generate_status_details(
                    new_obj["id"], version, message
                )
                successes.append(status_details)
                succeeded += 1

            status = generate_status(
                datetime_to_string(request_time), "complete", succeeded,
                failed, pending, successes=successes,
                failures=failures,
            )
            self._add_status(api_root, status)
            session.commit()
            return status

        except Exception as e:
            session.rollback()
            raise ProcessingError("While processing supplied content, an error occurred", 422, e)
        finally:
            session.close()

    def get_status(self, api_root, status_id):
        session = self.session()
        try:
            status = session.query(Status).filter_by(id=status_id, api_root=api_root).first()
            if status:
                return {
                    "id": status.id,
                    "status": status.status,
                    "request_timestamp": datetime_to_string(status.request_timestamp),
                    "total_count": status.total_count,
                    "success_count": status.success_count,
                    "failure_count": status.failure_count,
                    "pending_count": status.pending_count,
                    "successes": status.successes,
                    "failures": status.failures,
                }
            return None
        finally:
            session.close()

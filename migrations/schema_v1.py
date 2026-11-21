from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        MetaData, String, Table, Text, UniqueConstraint, JSON)

schema = MetaData()

def pk():
    return Column("id", Integer, primary_key=True)

def fk(name, table, nullable=False):
    return Column(name, ForeignKey(table + ".id"), nullable=nullable)

users = Table("users", schema, pk(), Column("email", String(254), unique=True, nullable=False),
              Column("password_hash", Text, nullable=False), Column("role", String(20), nullable=False),
              Column("environments", JSON, nullable=False, default=lambda: ["development"]))
sessions = Table("sessions", schema, Column("token_hash", String(64), primary_key=True),
                 fk("user_id", "users"), Column("expires", Float, nullable=False))
services = Table("services", schema, pk(), Column("name", String(63), nullable=False),
                 Column("environment", String(63), nullable=False), Column("repository", Text, nullable=False),
                 Column("namespace", String(63), nullable=False), Column("deployment", String(63), nullable=False),
                 Column("container", String(63), nullable=False), Column("approval", Boolean, nullable=False),
                 Column("policy", JSON, nullable=False), Column("healthy_image", Text),
                 Column("healthy_release", Integer), Column("active", Integer),
                 Column("applied_image", Text), Column("applied_release", Integer),
                 UniqueConstraint("name", "environment"), UniqueConstraint("namespace", "deployment"))
artifacts = Table("artifacts", schema, pk(), fk("service_id", "services"),
                  Column("image", Text, nullable=False), Column("sha", String(40), nullable=False),
                  Column("run_id", String(32), nullable=False), UniqueConstraint("service_id", "run_id"))
releases = Table("releases", schema, pk(), fk("service_id", "services"), fk("artifact_id", "artifacts"),
                 fk("requester", "users"), Column("request_key", String(100), nullable=False),
                 Column("status", String(32), nullable=False), Column("previous_image", Text),
                 Column("previous_release", Integer), Column("created", Float, nullable=False),
                 Column("since", Float, nullable=False), Column("due", Float, nullable=False),
                 UniqueConstraint("requester", "request_key"))
events = Table("audit_events", schema, pk(), fk("release_id", "releases", True),
               fk("actor", "users", True), Column("action", Text, nullable=False),
               Column("details", JSON, nullable=False), Column("time", Float, nullable=False))

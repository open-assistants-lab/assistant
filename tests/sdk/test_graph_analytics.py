"""Tests for HybridDB graph and analytics expansion.

Covers: Graph CRUD, traversal, NetworkX algorithms, DuckDB analytics,
and a performance benchmark comparing HybridDB.analytics() vs native DuckDB.
"""

import hashlib
import shutil
import tempfile

import pytest
from hybriddb import HybridDB, SearchMode
from hybriddb.embedding import EMBEDDING_DIM

networkx = pytest.importorskip("networkx")


def _mock_embedding(text: str) -> list[float]:
    if not text:
        return [0.0] * EMBEDDING_DIM
    words = str(text).lower().split()
    dim = EMBEDDING_DIM
    embedding = [0.0] * dim
    for word in words:
        h = int(hashlib.md5(word.encode()).hexdigest(), 16) % dim
        embedding[h] += 1.0
    mag = sum(x**2 for x in embedding) ** 0.5
    if mag > 0:
        embedding = [x / mag for x in embedding]
    return embedding


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db(tmp_dir):
    return HybridDB(tmp_dir, embedding_fn=_mock_embedding)


@pytest.fixture
def graph_db(db):
    db.add_node("A", label="Alice", type="person")
    db.add_node("B", label="Bob", type="person")
    db.add_node("C", label="Charlie", type="person")
    db.add_node("D", label="Diana", type="person")
    db.add_node("E", label="ProjectX", type="project")
    db.add_node("F", label="ProjectY", type="project")
    return db


# ── Graph CRUD: Nodes ────────────────────────────────────────


class TestGraphNodeCRUD:
    def test_add_node(self, db):
        nid = db.add_node("n1", label="TestNode", type="test", properties={"key": "val"})
        assert nid == "n1"
        node = db.get_node("n1")
        assert node is not None
        assert node["label"] == "TestNode"
        assert node["type"] == "test"
        assert node["properties"]["key"] == "val"

    def test_add_node_defaults(self, db):
        db.add_node("n2")
        node = db.get_node("n2")
        assert node["label"] == ""
        assert node["type"] == "node"
        assert node["properties"] == {}

    def test_add_node_upsert(self, db):
        db.add_node("n3", label="First")
        db.add_node("n3", label="Second")
        node = db.get_node("n3")
        assert node["label"] == "Second"

    def test_get_node_missing(self, db):
        assert db.get_node("nonexistent") is None

    def test_update_node_label(self, db):
        db.add_node("n4", label="Old")
        ok = db.update_node("n4", {"label": "New"})
        assert ok
        assert db.get_node("n4")["label"] == "New"

    def test_update_node_properties_merge(self, db):
        db.add_node("n5", properties={"a": 1, "b": 2})
        db.update_node("n5", {"properties": {"b": 99, "c": 3}})
        props = db.get_node("n5")["properties"]
        assert props["a"] == 1
        assert props["b"] == 99
        assert props["c"] == 3

    def test_update_node_missing(self, db):
        assert not db.update_node("ghost", {"label": "x"})

    def test_delete_node(self, db):
        db.add_node("n6")
        assert db.delete_node("n6")
        assert db.get_node("n6") is None

    def test_delete_node_cascades_edges(self, db):
        db.add_node("n7")
        db.add_node("n8")
        db.add_edge(None, "n7", "n8")
        db.delete_node("n7")
        assert len(db.get_edges(source_id="n7")) == 0

    def test_delete_node_missing(self, db):
        assert not db.delete_node("ghost")

    def test_list_nodes(self, db):
        db.add_node("a", type="t1")
        db.add_node("b", type="t2")
        db.add_node("c", type="t1")
        all_nodes = db.list_nodes()
        assert len(all_nodes) == 3
        t1_nodes = db.list_nodes(type="t1")
        assert len(t1_nodes) == 2

    def test_add_nodes_batch(self, db):
        ids = db.add_nodes([
            {"id": "batch1", "label": "B1", "type": "batch"},
            {"id": "batch2", "label": "B2", "type": "batch"},
            {"id": "batch3", "label": "B3"},
        ])
        assert len(ids) == 3
        assert db.get_node("batch1") is not None
        assert db.get_node("batch3")["type"] == "node"


# ── Graph CRUD: Edges ────────────────────────────────────────


class TestGraphEdgeCRUD:
    def test_add_edge(self, db):
        db.add_node("s1")
        db.add_node("t1")
        eid = db.add_edge(None, "s1", "t1", type="reports_to", weight=0.8)
        assert len(eid) > 0
        edge = db.get_edge(eid)
        assert edge["source_id"] == "s1"
        assert edge["target_id"] == "t1"
        assert edge["type"] == "reports_to"
        assert edge["weight"] == 0.8

    def test_add_edge_explicit_id(self, db):
        db.add_node("s2")
        db.add_node("t2")
        eid = db.add_edge("my_edge", "s2", "t2")
        assert eid == "my_edge"
        assert db.get_edge("my_edge") is not None

    def test_get_edge_missing(self, db):
        assert db.get_edge("no_edge") is None

    def test_update_edge(self, db):
        db.add_node("s3")
        db.add_node("t3")
        eid = db.add_edge(None, "s3", "t3", weight=1.0)
        ok = db.update_edge(eid, {"weight": 0.5, "type": "updated"})
        assert ok
        edge = db.get_edge(eid)
        assert edge["weight"] == 0.5
        assert edge["type"] == "updated"

    def test_update_edge_properties_merge(self, db):
        db.add_node("s4")
        db.add_node("t4")
        eid = db.add_edge(None, "s4", "t4", properties={"x": 1})
        db.update_edge(eid, {"properties": {"y": 2}})
        props = db.get_edge(eid)["properties"]
        assert props["x"] == 1
        assert props["y"] == 2

    def test_update_edge_missing(self, db):
        assert not db.update_edge("no_edge", {"weight": 0.1})

    def test_delete_edge(self, db):
        db.add_node("s5")
        db.add_node("t5")
        eid = db.add_edge(None, "s5", "t5")
        assert db.delete_edge(eid)
        assert db.get_edge(eid) is None

    def test_delete_edge_missing(self, db):
        assert not db.delete_edge("no_edge")

    def test_get_edges_by_source(self, db):
        db.add_node("a1")
        db.add_node("b1")
        db.add_node("c1")
        db.add_edge("e1", "a1", "b1")
        db.add_edge("e2", "a1", "c1")
        edges = db.get_edges(source_id="a1")
        assert len(edges) == 2

    def test_get_edges_by_type(self, db):
        db.add_node("a2")
        db.add_node("b2")
        db.add_node("c2")
        db.add_edge("e3", "a2", "b2", type="reports_to")
        db.add_edge("e4", "a2", "c2", type="collaborates")
        edges = db.get_edges(type="reports_to")
        assert len(edges) == 1
        assert edges[0]["id"] == "e3"

    def test_add_edges_batch(self, db):
        db.add_node("a3")
        db.add_node("b3")
        db.add_node("c3")
        ids = db.add_edges([
            {"source_id": "a3", "target_id": "b3", "type": "t1", "weight": 0.5},
            {"source_id": "a3", "target_id": "c3", "type": "t2", "weight": 0.9},
        ])
        assert len(ids) == 2
        all_edges = db.get_edges(source_id="a3")
        assert len(all_edges) == 2


# ── Graph: Traversal ─────────────────────────────────────────


class TestGraphTraversal:
    def test_neighbors_out(self, graph_db):
        graph_db.add_edge("e1", "A", "B", type="knows")
        graph_db.add_edge("e2", "A", "C", type="knows")
        neighbors = graph_db.neighbors("A", direction="out")
        assert len(neighbors) == 2
        ids = {n["node"]["id"] for n in neighbors}
        assert ids == {"B", "C"}

    def test_neighbors_in(self, graph_db):
        graph_db.add_edge("e3", "B", "A")
        graph_db.add_edge("e4", "C", "A")
        neighbors = graph_db.neighbors("A", direction="in")
        ids = {n["node"]["id"] for n in neighbors}
        assert ids == {"B", "C"}

    def test_neighbors_both(self, graph_db):
        graph_db.add_edge("e5", "A", "B")
        graph_db.add_edge("e6", "C", "A")
        neighbors = graph_db.neighbors("A", direction="both")
        ids = {n["node"]["id"] for n in neighbors}
        assert ids == {"B", "C"}

    def test_neighbors_by_type(self, graph_db):
        graph_db.add_edge("e7", "A", "B", type="friend")
        graph_db.add_edge("e8", "A", "C", type="colleague")
        friends = graph_db.neighbors("A", direction="out", type="friend")
        assert len(friends) == 1
        assert friends[0]["node"]["id"] == "B"

    def test_neighbors_no_connections(self, graph_db):
        assert graph_db.neighbors("A") == []

    def test_traverse_linear_chain(self, db):
        for nid in ["n1", "n2", "n3", "n4"]:
            db.add_node(nid)
        db.add_edge("c1", "n1", "n2")
        db.add_edge("c2", "n2", "n3")
        db.add_edge("c3", "n3", "n4")
        results = db.traverse("n1", max_depth=3, direction="out")
        ids = {r["node_id"] for r in results}
        assert "n2" in ids
        assert "n3" in ids
        assert "n4" in ids

    def test_traverse_depth_limit(self, db):
        for nid in ["n1", "n2", "n3"]:
            db.add_node(nid)
        db.add_edge("c1", "n1", "n2")
        db.add_edge("c2", "n2", "n3")
        results = db.traverse("n1", max_depth=1, direction="out")
        ids = {r["node_id"] for r in results}
        assert "n2" in ids
        assert "n3" not in ids

    def test_traverse_no_edges(self, graph_db):
        results = graph_db.traverse("A")
        assert results == []

    def test_traverse_invalid_depth(self, graph_db):
        with pytest.raises(ValueError):
            graph_db.traverse("A", max_depth=0)
        with pytest.raises(ValueError):
            graph_db.traverse("A", max_depth=11)


# ── NetworkX Graph Algorithms ────────────────────────────────


class TestNetworkXAlgorithms:
    def test_to_networkx(self, graph_db):
        graph_db.add_edge("e1", "A", "B", type="knows", weight=0.5)
        graph_db.add_edge("e2", "B", "C", type="knows", weight=0.7)
        g = graph_db.to_networkx(directed=True)
        assert g.number_of_nodes() == 6
        assert g.number_of_edges() == 2
        assert g.is_directed()

    def test_to_networkx_undirected(self, graph_db):
        graph_db.add_edge("e1", "A", "B")
        g = graph_db.to_networkx(directed=False)
        assert not g.is_directed()

    def test_pagerank(self, graph_db):
        graph_db.add_edge("e1", "A", "B")
        graph_db.add_edge("e2", "B", "A")
        graph_db.add_edge("e3", "B", "C")
        ranks = graph_db.pagerank()
        assert "A" in ranks
        assert "B" in ranks
        assert "C" in ranks
        assert all(v > 0 for v in ranks.values())

    def test_betweenness_centrality(self, graph_db):
        graph_db.add_edge("e1", "A", "B")
        graph_db.add_edge("e2", "B", "C")
        graph_db.add_edge("e3", "C", "A")
        bc = graph_db.betweenness_centrality()
        assert "A" in bc
        assert "B" in bc
        assert "C" in bc

    def test_shortest_path_exists(self, graph_db):
        graph_db.add_edge("e1", "A", "B", weight=1.0)
        graph_db.add_edge("e2", "B", "C", weight=1.0)
        path = graph_db.shortest_path("A", "C")
        assert path == ["A", "B", "C"]

    def test_shortest_path_no_path(self, graph_db):
        assert graph_db.shortest_path("A", "Z") is None

    def test_connected_components(self, graph_db):
        graph_db.add_edge("e1", "A", "B")
        graph_db.add_edge("e2", "C", "D")
        comps = graph_db.connected_components()
        component_sets = [set(c) for c in comps]
        found_ab = any({"A", "B"}.issubset(s) for s in component_sets)
        found_cd = any({"C", "D"}.issubset(s) for s in component_sets)
        assert found_ab
        assert found_cd

    def test_community_detect(self, graph_db):
        graph_db.add_edge("e1", "A", "B")
        graph_db.add_edge("e2", "B", "C")
        graph_db.add_edge("e3", "A", "C")
        graph_db.add_edge("e4", "D", "E")
        communities = graph_db.community_detect()
        assert len(communities) >= 1
        all_nodes = set()
        for c in communities:
            all_nodes.update(c)
        for nid in ["A", "B", "C", "D", "E"]:
            assert nid in all_nodes


# ── Analytics ────────────────────────────────────────────────


class TestAnalytics:
    def test_register_syncs_existing(self, db):
        db.create_table("events", {"name": "TEXT", "count": "INTEGER"})
        db.insert("events", {"name": "click", "count": 10})
        db.insert("events", {"name": "view", "count": 25})
        db.insert("events", {"name": "click", "count": 15})
        assert db.register_duckdb_table("events")
        results = db.analytics(
            "SELECT name, SUM(count) as total FROM events GROUP BY name"
        )
        totals = {r["name"]: r["total"] for r in results}
        assert totals["click"] == 25
        assert totals["view"] == 25

    def test_window_function(self, db):
        db.create_table(
            "items",
            {"name": "TEXT", "price": "REAL"},
        )
        db.insert_batch("items", [
            {"name": "apple", "price": 1.0},
            {"name": "banana", "price": 2.0},
            {"name": "cherry", "price": 3.0},
        ])
        assert db.register_duckdb_table("items")
        results = db.analytics("""
            SELECT name, price,
                   ROW_NUMBER() OVER (ORDER BY price DESC) as rank
            FROM items
        """)
        assert results[0]["rank"] == 1
        assert results[0]["price"] == 3.0

    def test_time_series(self, db):
        db.create_table(
            "logs",
            {"ts": "TEXT", "msg": "TEXT"},
        )
        for i in range(20):
            month = (i % 12) + 1
            db.insert("logs", {
                "ts": f"2026-{month:02d}-01T00:00:00Z",
                "msg": f"event_{i}",
            })
        assert db.register_duckdb_table("logs")
        results = db.analytics("""
            SELECT strftime(CAST(ts AS TIMESTAMP), '%Y-%m') as month,
                   COUNT(*) as count
            FROM logs
            GROUP BY month
            ORDER BY month
        """)
        assert len(results) > 0
        total_count = sum(r["count"] for r in results)
        assert total_count == 20

    def test_cross_table_join(self, db):
        db.create_table("people", {"name": "TEXT", "dept": "TEXT"})
        db.create_table("salaries", {"person": "TEXT", "amount": "REAL"})
        db.insert("people", {"name": "Alice", "dept": "Eng"})
        db.insert("people", {"name": "Bob", "dept": "Eng"})
        db.insert("salaries", {"person": "Alice", "amount": 100000.0})
        db.insert("salaries", {"person": "Bob", "amount": 90000.0})
        assert db.register_duckdb_table("people")
        assert db.register_duckdb_table("salaries")
        results = db.analytics("""
            SELECT p.dept, AVG(s.amount) as avg_salary
            FROM people p
            JOIN salaries s ON p.name = s.person
            GROUP BY p.dept
        """)
        assert len(results) == 1
        assert results[0]["avg_salary"] == 95000.0

    def test_journal_row_entries_sync_to_duckdb(self, db):
        db.create_table(
            "items",
            {"name": "TEXT", "count": "INTEGER"},
        )
        db.insert("items", {"name": "initial", "count": 1})
        assert db.register_duckdb_table("items")
        db.insert("items", {"name": "second", "count": 2})
        results = db.analytics("SELECT COUNT(*) as cnt FROM items")
        assert results[0]["cnt"] == 2

    def test_journal_delete_syncs_to_duckdb(self, db):
        db.create_table(
            "items",
            {"name": "TEXT", "count": "INTEGER"},
        )
        rid = db.insert("items", {"name": "keep", "count": 1})
        db.insert("items", {"name": "delete_me", "count": 2})
        assert db.register_duckdb_table("items")
        db.delete("items", rid)
        results = db.analytics("SELECT name FROM items")
        names = {r["name"] for r in results}
        assert "keep" not in names
        assert "delete_me" in names

    def test_journal_update_syncs_to_duckdb(self, db):
        db.create_table(
            "items",
            {"name": "TEXT", "count": "INTEGER"},
        )
        rid = db.insert("items", {"name": "old", "count": 1})
        assert db.register_duckdb_table("items")
        db.update("items", rid, {"name": "new", "count": 99})
        results = db.analytics(f"SELECT name, count FROM items WHERE id = {rid}")
        assert results[0]["name"] == "new"
        assert results[0]["count"] == 99

    def test_add_column_refreshes_registered_duckdb_table(self, db):
        db.create_table("items", {"name": "TEXT"})
        db.insert("items", {"name": "one"})
        assert db.register_duckdb_table("items")

        db.add_column("items", "count", "INTEGER")
        db.insert("items", {"name": "two", "count": 2})

        results = db.analytics("SELECT name, count FROM items ORDER BY id")
        assert results == [
            {"name": "one", "count": None},
            {"name": "two", "count": 2},
        ]

    def test_rename_column_refreshes_registered_duckdb_table(self, db):
        db.create_table("items", {"name": "TEXT", "count": "INTEGER"})
        db.insert("items", {"name": "one", "count": 1})
        assert db.register_duckdb_table("items")

        db.rename_column("items", "count", "quantity")

        results = db.analytics("SELECT name, quantity FROM items")
        assert results == [{"name": "one", "quantity": 1}]

    def test_drop_column_refreshes_registered_duckdb_table(self, db):
        db.create_table("items", {"name": "TEXT", "count": "INTEGER"})
        db.insert("items", {"name": "one", "count": 1})
        assert db.register_duckdb_table("items")

        db.drop_column("items", "count")

        results = db.analytics("SELECT * FROM items")
        assert results == [{"id": 1, "name": "one"}]

    def test_analytics_does_not_set_threads_per_query(self, db):
        class DuckDBSpy:
            def __init__(self, conn):
                self.conn = conn
                self.set_threads_calls = 0

            def execute(self, sql, *args, **kwargs):
                if str(sql).strip().upper().startswith("SET THREADS"):
                    self.set_threads_calls += 1
                return self.conn.execute(sql, *args, **kwargs)

        db.create_table("items", {"name": "TEXT"})
        db.insert("items", {"name": "one"})
        assert db.register_duckdb_table("items")
        spy = DuckDBSpy(db._duckdb_conn)
        db._duckdb_conn = spy

        db.analytics("SELECT * FROM items")
        db.analytics("SELECT * FROM items")

        assert spy.set_threads_calls == 0

    def test_unregister_table(self, db):
        db.create_table("temp", {"name": "TEXT"})
        db.insert("temp", {"name": "x"})
        assert db.register_duckdb_table("temp")
        assert db.unregister_duckdb_table("temp")



# ── Regression: Existing features unaffected ─────────────────


class TestGraphDoesNotBreakExisting:
    def test_list_tables_excludes_graph(self, db):
        db.create_table("t1", {"name": "TEXT"})
        tables = db.list_tables()
        assert "t1" in tables
        assert "_graph_nodes" not in tables
        assert "_graph_edges" not in tables
        assert "_journal" not in tables

    def test_insert_search_still_works(self, db):
        db.create_table(
            "contacts",
            {
                "first_name": "TEXT",
                "notes": "LONGTEXT",
            },
        )
        db.insert("contacts", {"first_name": "Alice", "notes": "VIP client for enterprise deals"})
        db.add_node("n1", label="Test")
        results = db.search("contacts", "notes", "enterprise", mode=SearchMode.KEYWORD)
        assert len(results) >= 1

    def test_journal_still_works(self, db):
        db.create_table("test", {"notes": "LONGTEXT"})
        db.insert("test", {"notes": "hello"}, sync=False)
        count = db._journal_count("test")
        assert count > 0
        db.process_journal()
        assert db._journal_count("test") == 0

    def test_reconcile_still_works(self, db):
        db.create_table("test", {"notes": "LONGTEXT"})
        db.insert("test", {"notes": "hello"})
        result = db.reconcile("test")
        assert "ghosts_deleted" in result

    def test_health_still_works(self, db):
        db.create_table("test", {"notes": "LONGTEXT"})
        db.insert("test", {"notes": "hello"})
        h = db.health("test")
        assert h["sqlite_rows"] == 1
        assert h["status"] in ("ok", "drift")


# ── Graph: Entity Sync & Edge Rules ───────────────────────────


class TestGraphSync:
    def test_register_entity_node(self, db):
        db.create_table("test_entities", {"name": "TEXT", "value": "INTEGER"})
        ok = db.register_entity_node(
            "test_entities", type="entity", id_column="id", label_template="Entity: {id}"
        )
        assert ok
        rows = db.raw_query("SELECT * FROM _graph_sync")
        assert len(rows) == 1

    def test_register_entity_node_missing_table(self, db):
        assert not db.register_entity_node("nonexistent_table")

    def test_auto_sync_creates_nodes(self, db):
        db.create_table("people", {"name": "TEXT"})
        db.register_entity_node("people", type="person", id_column="id", label_template="Person: {id}")
        rid1 = db.insert("people", {"name": "Alice"})
        rid2 = db.insert("people", {"name": "Bob"})

        db.reconcile("people")
        nodes = db.list_nodes(type="person")
        assert len(nodes) == 2
        node_ids = {n["id"] for n in nodes}
        # hybriddb 0.5.5: auto-synced node ids are namespaced {table}:{pk}
        assert f"people:{rid1}" in node_ids
        assert f"people:{rid2}" in node_ids

    def test_register_edge_rule(self, db):
        db.create_table("people", {"name": "TEXT"})
        db.create_table("companies", {"name": "TEXT", "contact_id": "INTEGER"})
        ok = db.register_edge_rule("people", "companies", "contact_id", "works_at")
        assert ok
        assert len(db.raw_query("SELECT * FROM _edge_rules")) == 1

    def test_auto_sync_creates_edges(self, db):
        db.create_table("people", {"name": "TEXT", "company_id": "INTEGER"})
        db.create_table("companies", {"name": "TEXT"})
        db.register_entity_node("people", type="person")
        db.register_entity_node("companies", type="company")
        db.register_edge_rule("people", "companies", "company_id", "works_at")

        rid_c = db.insert("companies", {"name": "Acme"})
        db.insert("people", {"name": "Alice", "company_id": rid_c})
        db.insert("people", {"name": "Bob", "company_id": rid_c})

        db.reconcile("people")
        edges = db.get_edges(type="works_at")
        assert len(edges) == 2

    def test_auto_sync_creates_edges_with_different_join_columns(self, db):
        db.create_table("emails", {"sender_email": "TEXT"})
        db.create_table("contacts", {"email": "TEXT"})
        db.register_entity_node("emails", type="email")
        db.register_entity_node("contacts", type="contact")
        db.register_edge_rule(
            "emails",
            "contacts",
            edge_type="sent_by",
            source_column="sender_email",
            target_column="email",
        )

        contact_id = db.insert("contacts", {"email": "alice@example.com"})
        email_id = db.insert("emails", {"sender_email": "alice@example.com"})

        db.reconcile("emails")
        edges = db.get_edges(type="sent_by")

        assert len(edges) == 1
        # hybriddb 0.5.5: synced node ids are namespaced {table}:{pk}
        assert edges[0]["source_id"] == f"emails:{email_id}"
        assert edges[0]["target_id"] == f"contacts:{contact_id}"

    def test_edge_rule_schema_migrates_existing_database(self, tmp_dir):
        db = HybridDB(tmp_dir, embedding_fn=_mock_embedding)
        with db._connect() as cur:
            cur.execute("DROP TABLE _edge_rules")
            cur.execute("""
                CREATE TABLE _edge_rules (
                    source_table TEXT NOT NULL,
                    target_table TEXT NOT NULL,
                    target_match TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    PRIMARY KEY (source_table, target_table, edge_type)
                )
            """)

        migrated = HybridDB(tmp_dir, embedding_fn=_mock_embedding)
        columns = {row["name"] for row in migrated.raw_query("PRAGMA table_info(_edge_rules)")}

        assert "source_column" in columns
        assert "target_column" in columns

    def test_search_graph_embeds_query_once_across_columns(self, db):
        calls = []

        def embedding_fn(text: str) -> list[float]:
            calls.append(text)
            return [0.0] * 384

        db._embedding_fn = embedding_fn
        db.create_table("people", {"bio": "LONGTEXT", "notes": "LONGTEXT"})
        db.insert("people", {"bio": "alpha", "notes": "beta"})
        db.register_entity_node("people", type="person")
        calls.clear()

        db.search_graph("alpha")

        assert calls.count("alpha") == 1


# ── Edge Decay ────────────────────────────────────────────────


class TestEdgeDecay:
    def test_decay_reduces_expired(self, db):
        from datetime import UTC, datetime, timedelta
        past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        db.add_node("n1")
        db.add_node("n2")
        db.add_edge(None, "n1", "n2", weight=1.0, valid_until=past)
        decayed = db.decay_edges()
        assert decayed == 1
        edge = db.get_edges(source_id="n1")[0]
        assert edge["weight"] == 0.85

    def test_decay_ignores_valid(self, db):
        from datetime import UTC, datetime, timedelta
        future = (datetime.now(UTC) + timedelta(days=10)).isoformat()
        db.add_node("n1")
        db.add_node("n2")
        db.add_edge(None, "n1", "n2", weight=1.0, valid_until=future)
        assert db.decay_edges() == 0

    def test_decay_deletes_at_floor(self, db):
        from datetime import UTC, datetime, timedelta
        past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        db.add_node("n1")
        db.add_node("n2")
        db.add_edge(None, "n1", "n2", weight=0.1, valid_until=past)
        db.decay_edges()
        assert len(db.get_edges(source_id="n1")) == 0

    def test_reconcile_cleans_dead_edges(self, db):
        from datetime import UTC, datetime, timedelta
        past = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        db.add_node("n1")
        db.add_node("n2")
        db.add_edge(None, "n1", "n2", weight=0.04, valid_until=past)
        db.create_table("ref_table", {"val": "TEXT"})
        db.insert("ref_table", {"val": "x"})
        db.reconcile("ref_table")
        assert len(db.get_edges(source_id="n1")) == 0


# ── NetworkX Cache ────────────────────────────────────────────


class TestNxCache:
    def test_cache_invalidation_on_add_node(self, db):
        db.add_node("n1")
        db.to_networkx(use_cache=True)
        assert not db._nx_cache["dirty"]
        db.add_node("n2")
        assert db._nx_cache["dirty"]

    def test_cache_invalidation_on_add_edge(self, db):
        db.add_node("n1")
        db.add_node("n2")
        db.to_networkx(use_cache=True)
        assert not db._nx_cache["dirty"]
        db.add_edge(None, "n1", "n2")
        assert db._nx_cache["dirty"]

    def test_cache_respects_directed_change(self, db):
        db.add_node("n1")
        db.add_node("n2")
        db.add_edge(None, "n1", "n2")
        db.to_networkx(directed=True, use_cache=True)
        assert db._nx_cache["directed"] is True
        gu = db.to_networkx(directed=False, use_cache=True)
        assert gu.number_of_nodes() == 2

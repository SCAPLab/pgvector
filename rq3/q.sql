\set qid random(1, 10000)
SELECT id FROM items
ORDER BY embedding <-> (SELECT v FROM queries WHERE id = :qid)
LIMIT 10;

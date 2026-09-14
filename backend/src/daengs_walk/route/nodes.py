"""Canonical continuity blocks, moving distance and exact source observations."""


def route_nodes(evidence):
    """Canonical continuity blocks and exact source observations, shared by diary preparation."""
    start = evidence.facts.started_at
    nodes, offset, block, previous = [], 0.0, -1, None

    def node(fix):
        return {
            "route_m": offset,
            "block": block,
            "elapsed_s": (fix.at - start).total_seconds(),
            "observation": {
                "client_seq": fix.client_seq,
                "chain_index": fix.chain_index,
                "at": fix.at.isoformat(),
                "lat": fix.lat,
                "lng": fix.lng,
            },
            "location": {
                "lat": fix.lat,
                "lng": fix.lng,
                "accuracy_m": fix.accuracy_m,
                "captured_at": fix.at.isoformat(),
            },
        }

    for segment in evidence.segments:
        if previous != (segment.chain_index, segment.a.client_seq):
            block += 1
            nodes.append(node(segment.a))
        offset += segment.dist if segment.moving else 0
        end = node(segment.b)
        end.update(
            speed=segment.dist / segment.dt,
            duration_s=segment.dt,
            start_s=(segment.a.at - start).total_seconds(),
        )
        nodes.append(end)
        previous = (segment.chain_index, segment.b.client_seq)
    return nodes

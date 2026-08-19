"""Iterative walks over a class hierarchy.

Three modules (``ontologyeval``, ``sketch``, ``dataquality``) each carried
their own copy of the same two functions, and ``versioning/diff.py`` a
fourth of the same shape. All four were recursive, and all four had the
same defect: they guarded *cycles* but not *descent*.

Hierarchy depth follows the longest ``rdfs:subClassOf`` chain in the input,
and a recursive walk costs one Python frame per link. CPython's default
recursion limit is 1000 -- an order of magnitude tighter than the JS
runtime where this class of bug was first found in the sibling VS Code
extension -- so a ~900-link chain was enough to raise ``RecursionError``
from what is only a metrics report. Measured, not estimated:

    ontologyeval.compute_metrics          RecursionError at depth 900
    sketch.ontology_quality.compute_metrics  RecursionError at depth 5000
    dataquality._ancestors                RecursionError at depth 1000
    versioning.diff._descendants_inclusive   RecursionError at depth 1000

Memoisation hid this whenever classes happened to be visited
shallowest-first, which is why it never showed up in practice: declare the
same chain in the other order -- just as valid a document -- and it threw.
That order-dependence is also why the thresholds above differ between
modules that run structurally identical code.

Two fixes were considered and rejected:

* ``sys.setrecursionlimit(N)``. It does not grow the actual C stack, so a
  limit high enough to cover a deep chain trades a catchable
  ``RecursionError`` for an uncatchable interpreter crash. Strictly worse:
  the failure stops being a traceback that names the input.
* A depth cap. Any limit low enough to be safe under a 1000-frame ceiling
  is also low enough to silently truncate a real answer.

Walking on the heap removes the ceiling rather than choosing one: a
100,000-deep chain now reports 100,000. Cycle handling, memo contents and
return values are unchanged from the recursive originals, including the
quirk that a result truncated by a cycle is still memoised.
"""
from __future__ import annotations

_MISSING = object()


def ancestors(node, parents_of, memo, cyclic=None):
    """Every transitive ``rdfs:subClassOf`` ancestor of ``node``.

    ``memo`` is shared across calls and mutated in place (the caller owns
    it, exactly as before). ``cyclic``, when given, collects the nodes at
    which a subclass cycle closes -- ``ontology_evaluation`` surfaces that
    set as its ``cyclic_classes`` report flag.
    """
    if node in memo:
        return memo[node]

    # frame = [node, unvisited-parents iterator, ancestors accumulated so far]
    stack = [[node, iter(parents_of.get(node, ())), set()]]
    on_path = {node}  # the recursive form's `visiting`, maintained explicitly

    while stack:
        frame = stack[-1]
        parent = next(frame[1], _MISSING)

        if parent is not _MISSING:
            frame[2].add(parent)
            if parent in memo:
                frame[2] |= memo[parent]
            elif parent in on_path:
                # Already being expanded further down the stack: contributes
                # nothing, same as the recursive form returning an empty set.
                if cyclic is not None:
                    cyclic.add(parent)
            else:
                on_path.add(parent)
                stack.append([parent, iter(parents_of.get(parent, ())), set()])
            continue

        current, _, accumulated = stack.pop()
        on_path.discard(current)
        memo[current] = accumulated
        if stack:
            stack[-1][2] |= accumulated

    return memo[node]


def depth(node, parents_of, memo):
    """Longest ``rdfs:subClassOf`` path from ``node`` up to a root (its
    depth-of-inheritance). 0 for a class with no declared superclass; a
    branch that closes a cycle contributes 0, as before."""
    if node in memo:
        return memo[node]

    # frame = [node, unvisited-parents iterator, deepest parent seen, has any parent]
    stack = [[node, iter(parents_of.get(node, ())), 0, False]]
    on_path = {node}

    while stack:
        frame = stack[-1]
        parent = next(frame[1], _MISSING)

        if parent is not _MISSING:
            frame[3] = True
            if parent in memo:
                frame[2] = max(frame[2], memo[parent])
            elif parent not in on_path:
                on_path.add(parent)
                stack.append([parent, iter(parents_of.get(parent, ())), 0, False])
            # else: a cycle, contributing 0 -- leave `deepest` alone
            continue

        current, _, deepest, has_parent = stack.pop()
        on_path.discard(current)
        memo[current] = (1 + deepest) if has_parent else 0
        if stack:
            stack[-1][2] = max(stack[-1][2], memo[current])

    return memo[node]


def descendants_inclusive(node, children_of, memo):
    """``node`` plus every transitive subclass of it.

    Walks down instead of up, and is otherwise the same shape. Seeding the
    memo with ``{node}`` before descending is what terminates a cycle --
    the recursive original's guard, kept verbatim.
    """
    if node in memo:
        return memo[node]

    memo[node] = {node}
    stack = [[node, iter(children_of.get(node, ())), {node}]]

    while stack:
        frame = stack[-1]
        child = next(frame[1], _MISSING)

        if child is not _MISSING:
            if child in memo:
                frame[2] |= memo[child]
            else:
                memo[child] = {child}
                stack.append([child, iter(children_of.get(child, ())), {child}])
            continue

        current, _, accumulated = stack.pop()
        memo[current] = accumulated
        if stack:
            stack[-1][2] |= accumulated

    return memo[node]

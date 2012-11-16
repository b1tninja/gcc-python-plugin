#   Copyright 2012 David Malcolm <dmalcolm@redhat.com>
#   Copyright 2012 Red Hat, Inc.
#
#   This is free software: you can redistribute it and/or modify it
#   under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful, but
#   WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#   General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see
#   <http://www.gnu.org/licenses/>.

############################################################################
# Preprocessing phase
############################################################################
import gcc

"""
class Fact:
    def __init__(self, lhs, op, rhs):
        self.lhs = lhs
        self.op = op
        self.rhs = rhs

class Facts:
    def __init__(self, set_):
        self.facts = set_
"""

def get_aliases(facts, var):
    result = set([var])
    for fact in facts:
        lhs, op, rhs = fact
        if op == '==':
            if lhs in result:
                result.add(rhs)
            if rhs in result:
                result.add(lhs)
    return result

def is_referenced_externally(ctxt, var, facts):
    ctxt.debug('is_referenced_externally(%s, %s)', var, facts)
    for fact in facts:
        lhs, op, rhs = fact
        if op == '==':
            # For now, any equality will do it
            # FIXME: needs to be something that isn't a local
            if var == lhs:
                return True
            if var == rhs:
                return True
    return False

inverseops =  {'==' : '!=',
               '!=' : '==',
               '<'  : '>=',
               '<=' : '>',
               '>'  : '<=',
               '>=' : '<',
               }

def get_facts_after(ctxt, srcfacts, edge):
    stmt = edge.srcnode.stmt
    dstfacts = set(srcfacts)
    if isinstance(stmt, gcc.GimpleAssign):
        if 1:
            ctxt.debug('gcc.GimpleAssign: %s', stmt)
            ctxt.debug('  stmt.lhs: %r', stmt.lhs)
            ctxt.debug('  stmt.rhs: %r', stmt.rhs)
            ctxt.debug('  stmt.exprcode: %r', stmt.exprcode)
        lhs = stmt.lhs
        rhs = stmt.rhs
        if isinstance(rhs[0], gcc.SsaName):
            rhs = rhs[0].var
        else:
            rhs = rhs[0]
        if isinstance(lhs, gcc.SsaName):
            lhs = lhs.var
        dstfacts.add( (lhs, '==', rhs) )
        # Eliminate any now-invalid facts:
        for fact in frozenset(dstfacts):
            pass # FIXME
    elif isinstance(stmt, gcc.GimpleCond):
        if 1:
            ctxt.debug('gcc.GimpleCond: %s', stmt)
        lhs = stmt.lhs
        rhs = stmt.rhs
        if isinstance(rhs, gcc.SsaName):
            rhs = rhs.var
        else:
            rhs = rhs
        if isinstance(lhs, gcc.SsaName):
            lhs = lhs.var
        op = stmt.exprcode.get_symbol()
        if edge.true_value:
            dstfacts.add( (lhs, op, rhs) )
        if edge.false_value:
            op = inverseops[op]
            dstfacts.add( (lhs, op, rhs) )
    return frozenset(dstfacts)

def find_facts(ctxt, graph):
    """
    Add a "facts" field to the nodes in the graph.

    Used as a preprocessing step on the supergraph, and also
    on the ErrorGraph for a specific error
    """
    ctxt.log('find_facts()')
    with ctxt.indent():
        worklist = []
        done = set()
        for node in graph.nodes:
            node.facts = frozenset()
        for node in graph.get_entry_nodes():
            worklist.append(node)
        while worklist:
            srcnode = worklist.pop()
            ctxt.debug('considering %s', srcnode)
            done.add(srcnode)
            ctxt.debug('len(done): %s', len(done))
            # ctxt.debug('done: %s', done)
            with ctxt.indent():
                srcfacts = srcnode.facts
                ctxt.debug('srcfacts: %s', srcfacts)
                for edge in srcnode.succs:
                    stmt = srcnode.stmt
                    dstnode = edge.dstnode

                    # Set the location so that if an unhandled exception occurs, it should
                    # at least identify the code that triggered it:
                    if stmt:
                        if stmt.loc:
                            gcc.set_location(stmt.loc)

                    ctxt.debug('considering edge to %s', dstnode)
                    with ctxt.indent():
                        if len(dstnode.preds) == 1:
                            ctxt.debug('dstnode has single pred; gathering known facts')
                            dstfacts = get_facts_after(ctxt, srcfacts, edge)
                            ctxt.debug('dstfacts: %s', dstfacts)
                            dstnode.facts = dstfacts
                        if dstnode not in done:
                            worklist.append(dstnode)

def is_possible(ctxt, facts):
    ctxt.debug('is_possible: %s', facts)
    # Work-in-progress implementation:
    # Gather vars by equivalence classes:

    # dict from expr to (shared) sets of exprs
    partitions = {}

    for fact in facts:
        lhs, op, rhs = fact
        if op == '==':
            if lhs in partitions:
                if rhs in partitions:
                    merged = partitions[lhs] | partitions[rhs]
                else:
                    partitions[lhs].add(rhs)
                    merged = partitions[lhs]
            else:
                if rhs in partitions:
                    partitions[rhs].add(lhs)
                    merged = partitions[rhs]
                else:
                    merged = set([lhs, rhs])
            partitions[lhs] = partitions[rhs] = merged

    ctxt.debug('partitions: %s', partitions)

    # There must be at most one specific constant within any equivalence
    # class:
    constants = {}
    for key in partitions:
        equivcls = partitions[key]
        for expr in equivcls:
            if isinstance(expr, gcc.IntegerCst):
                if key in constants:
                    # More than one (non-equal) constant within the class:
                    ctxt.debug('impossible: equivalence class for %s'
                               ' contains non-equal constants %s and %s'
                               % (equivcls, constants[key], expr))
                    return False
                constants[key] = expr

    ctxt.debug('constants: %s' % constants)

    # Check any such constants against other inequalities:
    for fact in facts:
        lhs, op, rhs = fact
        if op in ('!=', '<', '>'):
            if isinstance(rhs, gcc.IntegerCst):
                if lhs in constants:
                    if constants[lhs] == rhs:
                        # a == CONST_1 && a != CONST_1 is impossible:
                        ctxt.debug('impossible: equivalence class for %s'
                                   ' equals constant %s but has %s %s %s',
                                   equivcls, constants[lhs], lhs, op, rhs)
                        return False

    # All tests passed:
    return True

def remove_impossible(ctxt, graph):
    # Purge graph of any nodes with contradictory facts which are thus
    # impossible to actually reach
    ctxt.log('remove_impossible')
    changes = 0
    for node in graph.nodes:
        if not is_possible(ctxt, node.facts):
            graph.remove_node(node)
            changes += 1
    ctxt.log('removed %i node(s)' % changes)
    return changes

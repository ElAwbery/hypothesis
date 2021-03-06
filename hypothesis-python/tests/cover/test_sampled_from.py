# coding=utf-8
#
# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Most of this work is copyright (C) 2013-2019 David R. MacIver
# (david@drmaciver.com), but it contains contributions by others. See
# CONTRIBUTING.rst for a full list of people who may hold copyright, and
# consult the git log if you need to determine who owns an individual
# contribution.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.
#
# END HEADER

from __future__ import absolute_import, division, print_function

import collections
import enum

from hypothesis import given
from hypothesis.errors import FailedHealthCheck, InvalidArgument, Unsatisfiable
from hypothesis.internal.compat import hrange
from hypothesis.strategies import sampled_from
from tests.common.utils import checks_deprecated_behaviour, fails_with

an_enum = enum.Enum("A", "a b c")

an_ordereddict = collections.OrderedDict([("a", 1), ("b", 2), ("c", 3)])


@fails_with(InvalidArgument)
def test_cannot_sample_sets():
    sampled_from(set("abc")).example()


def test_can_sample_sequence_without_warning():
    sampled_from([1, 2, 3]).example()


def test_can_sample_ordereddict_without_warning():
    sampled_from(an_ordereddict).example()


@given(sampled_from(an_enum))
def test_can_sample_enums(member):
    assert isinstance(member, an_enum)


@checks_deprecated_behaviour
def test_sampling_empty_is_deprecated():
    assert sampled_from([]).is_empty


@fails_with(FailedHealthCheck)
@given(sampled_from(hrange(10)).filter(lambda x: x < 0))
def test_unsat_filtered_sampling(x):
    assert False


@fails_with(Unsatisfiable)
@given(sampled_from(hrange(2)).filter(lambda x: x < 0))
def test_unsat_filtered_sampling_in_rejection_stage(x):
    # Rejecting all possible indices before we calculate the allowed indices
    # takes an early exit path, so we need this test to cover that branch.
    assert False


def test_easy_filtered_sampling():
    x = sampled_from(hrange(100)).filter(lambda x: x == 0).example()
    assert x == 0


@given(sampled_from(hrange(100)).filter(lambda x: x == 99))
def test_filtered_sampling_finds_rare_value(x):
    assert x == 99

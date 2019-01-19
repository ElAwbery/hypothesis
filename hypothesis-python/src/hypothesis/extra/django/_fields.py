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

import re
import string
from datetime import timedelta
from decimal import Decimal

import django
import django.db.models as dm
import django.forms as df
from django.core.validators import (
    validate_ipv4_address,
    validate_ipv6_address,
    validate_ipv46_address,
)

import hypothesis.strategies as st
from hypothesis.errors import InvalidArgument
from hypothesis.extra.pytz import timezones
from hypothesis.internal.validation import check_type
from hypothesis.provisional import ip4_addr_strings, ip6_addr_strings, urls
from hypothesis.strategies import emails

if False:
    from datetime import tzinfo  # noqa
    from typing import Any, Type, Optional, List, Text, Callable, Union  # noqa


def get_tz_strat():
    # type: () -> st.SearchStrategy[Optional[tzinfo]]
    if getattr(django.conf.settings, "USE_TZ", False):
        return timezones()
    return st.none()


# Mapping of field types, to strategy objects or functions of (type) -> strategy
_global_field_lookup = {
    dm.SmallIntegerField: st.integers(-32768, 32767),
    dm.IntegerField: st.integers(-2147483648, 2147483647),
    dm.BigIntegerField: st.integers(-9223372036854775808, 9223372036854775807),
    dm.PositiveIntegerField: st.integers(0, 2147483647),
    dm.PositiveSmallIntegerField: st.integers(0, 32767),
    dm.BinaryField: st.binary(),
    dm.BooleanField: st.booleans(),
    dm.DateField: st.dates(),
    dm.EmailField: emails(),
    dm.FloatField: st.floats(),
    dm.NullBooleanField: st.one_of(st.none(), st.booleans()),
    dm.URLField: urls(),
    dm.UUIDField: st.uuids(),
    df.BooleanField: st.booleans(),
    df.BooleanField: st.sampled_from(["0", "1"]),
    df.DateField: st.dates(),
    df.DateTimeField: st.datetimes(timezones=get_tz_strat()),
    df.DurationField: st.timedeltas(),
    df.EmailField: emails(),
    df.FloatField: st.floats(allow_nan=False, allow_infinity=False),
    df.IntegerField: st.integers(-2147483648, 2147483647),
    df.NullBooleanField: st.one_of(st.none(), st.booleans()),
    df.TimeField: st.times(timezones=get_tz_strat()),
    df.URLField: urls(),
    df.UUIDField: st.uuids(),
}


def register_for(field_type):
    def inner(func):
        _global_field_lookup[field_type] = func
        return func

    return inner


@register_for(dm.DateTimeField)
def _for_datetime(field):
    if getattr(django.conf.settings, "USE_TZ", False):
        return st.datetimes(timezones=timezones())
    return st.datetimes()


def using_sqlite():
    try:
        return (
            getattr(django.conf.settings, "DATABASES", {})
            .get("default", {})
            .get("ENGINE", "")
            .endswith(".sqlite3")
        )
    except django.core.exceptions.ImproperlyConfigured:
        return None


@register_for(dm.TimeField)
def _for_time(field):
    # SQLITE supports TZ-aware datetimes, but not TZ-aware times.
    if getattr(django.conf.settings, "USE_TZ", False) and not using_sqlite():
        return st.times(timezones=timezones())
    return st.times()


@register_for(dm.DurationField)
def _for_duration(field):
    # SQLite stores timedeltas as six bytes of microseconds
    if using_sqlite():
        delta = timedelta(microseconds=2 ** 47 - 1)
        return st.timedeltas(-delta, delta)
    return st.timedeltas()


@register_for(dm.SlugField)
@register_for(df.SlugField)
def _for_slug(field):
    min_size = 1
    if getattr(field, "blank", False) or not getattr(field, "required", True):
        min_size = 0
    return st.text(
        alphabet=string.ascii_letters + string.digits,
        min_size=min_size,
        max_size=field.max_length,
    )


def _for_ip(protocol):
    return dict(
        ipv4=ip4_addr_strings(),
        ipv6=ip6_addr_strings(),
        both=ip4_addr_strings() | ip6_addr_strings(),
    )[protocol]


@register_for(dm.GenericIPAddressField)
def _for_model_ip(field):
    return _for_ip(field.protocol.lower())


@register_for(df.GenericIPAddressField)
def _for_form_ip(field):
    # the IP address form fields have no direct indication of which type
    #  of address they want, so direct comparison with the validator
    #  function has to be used instead. Sorry for the potato logic here
    dv = field.default_validators
    protocol = "unkown"
    if validate_ipv46_address in dv:
        protocol = "both"
    elif validate_ipv4_address in dv:
        protocol = "ipv4"
    elif validate_ipv6_address in dv:
        protocol = "ipv6"
    return _for_ip(protocol)


@register_for(dm.DecimalField)
@register_for(df.DecimalField)
def _for_decimal(field):
    bound = Decimal(10 ** field.max_digits - 1) / (10 ** field.decimal_places)
    return st.decimals(min_value=-bound, max_value=bound, places=field.decimal_places)


@register_for(dm.CharField)
@register_for(dm.TextField)
@register_for(df.CharField)
@register_for(df.RegexField)
def _for_text(field):
    # We can infer a vastly more precise strategy by considering the
    # validators as well as the field type.  This is a minimal proof of
    # concept, but we intend to leverage the idea much more heavily soon.
    # See https://github.com/HypothesisWorks/hypothesis-python/issues/1116
    regexes = [
        re.compile(v.regex, v.flags) if isinstance(v.regex, str) else v.regex
        for v in field.validators
        if isinstance(v, django.core.validators.RegexValidator) and not v.inverse_match
    ]
    if regexes:
        # This strategy generates according to one of the regexes, and
        # filters using the others.  It can therefore learn to generate
        # from the most restrictive and filter with permissive patterns.
        # Not maximally efficient, but it makes pathological cases rarer.
        # If you want a challenge: extend https://qntm.org/greenery to
        # compute intersections of the full Python regex language.
        return st.one_of(*[st.from_regex(r) for r in regexes])
    # If there are no (usable) regexes, we use a standard text strategy.
    min_size = 1
    if getattr(field, "blank", False) or not getattr(field, "required", True):
        min_size = 0
    return st.text(
        alphabet=st.characters(
            blacklist_characters=u"\x00", blacklist_categories=("Cs",)
        ),
        min_size=min_size,
        max_size=field.max_length,
    )


def register_field_strategy(field_type, strategy):
    # type: (Type[dm.Field], st.SearchStrategy) -> None
    """Add an entry to the global field-to-strategy lookup used by from_field.

    ``field_type`` must be a subtype of django.db.models.Field, which must not
    already be registered.  ``strategy`` must be a SearchStrategy.
    """
    if not issubclass(field_type, dm.Field):
        raise InvalidArgument(
            "field_type=%r must be a subtype of Field" % (field_type,)
        )
    check_type(st.SearchStrategy, strategy, "strategy")
    if field_type in _global_field_lookup:
        raise InvalidArgument(
            "field_type=%r already has a registered strategy (%r)"
            % (field_type, _global_field_lookup[field_type])
        )
    if issubclass(field_type, dm.AutoField):
        raise InvalidArgument("Cannot register a strategy for an AutoField")
    _global_field_lookup[field_type] = strategy


def from_field(field):
    # type: (Type[dm.Field]) -> st.SearchStrategy[dm.Field]
    """Return a strategy for values that fit the given field.

    This is pretty similar to the core `from_type` function, with a subtle
    but important difference: `from_field` takes a Field *instance*, rather
    than a Field *subtype*, so that it has access to instance attributes
    such as string length and validators.
    """
    check_type((dm.Field, df.Field), field, "field")
    # not all form fields have ``choices``, so this will raise an exception
    # if field.choices:
    if getattr(field, "choices", False):
        choices = []  # type: list
        for value, name_or_optgroup in field.choices:
            if isinstance(name_or_optgroup, (list, tuple)):
                choices.extend(key for key, _ in name_or_optgroup)
            else:
                choices.append(value)
        # form fields automatically include the blank option, strip it out
        if "" in choices:
            choices.remove("")
        min_size = 1
        if isinstance(field, (dm.CharField, dm.TextField)) and field.blank:
            choices.insert(0, u"")
            pass
        # form fields don't have ``blank``, but they do have required
        elif isinstance(field, (df.Field)) and not field.required:
            choices.insert(0, u"")
            min_size = 0
        strategy = st.sampled_from(choices)
        # TODO: test this
        if isinstance(field, (df.MultipleChoiceField, df.TypedMultipleChoiceField)):
            # not quite sure how the field will recieve multiples
            strategy = st.lists(st.sampled_from(choices), min_size=min_size)
    # elif isinstance(field, df.ComboField):
    #     # introspect further
    #     strats = []
    #     for _field in field.fields:
    #         strats.append(from_field(_field))
    # # This requires unioning together two or more strategies, which
    # # I've seen done somewhere in the documentation, but I cannot find
    # # it right now
    #     strategy = Union(*strats)
    else:
        if type(field) not in _global_field_lookup:
            # form fields don't have ``null``
            if getattr(field, "null", False):
                return st.none()
            raise InvalidArgument("Could not infer a strategy for %r", (field,))
        strategy = _global_field_lookup[type(field)]
        if not isinstance(strategy, st.SearchStrategy):
            strategy = strategy(field)
    assert isinstance(strategy, st.SearchStrategy)
    if field.validators:

        def validate(value):
            try:
                field.run_validators(value)
                return True
            except django.core.exceptions.ValidationError:
                return False

        strategy = strategy.filter(validate)

    if getattr(field, "null", False):
        return st.none() | strategy
    return strategy

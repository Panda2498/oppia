# coding: utf-8
#
# Copyright 2013 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Model for an Oppia exploration."""

__author__ = 'Sean Lip'

import os

from base_model import BaseModel
import feconf
import logging
from models.image import Image
from state import State
import utils

from google.appengine.ext import ndb
from google.appengine.api import users


class Parameter(ndb.Model):
    """A parameter definition for an exploration."""
    # The name of the parameter
    name = ndb.StringProperty(required=True)
    # The data type of the parameter - for now only unicode or list
    param_type = ndb.StringProperty(default='unicode')


# TODO(sll): Add an anyone-can-edit mode.
class Exploration(BaseModel):
    """An exploration (which is made up of several states)."""
    # The category this exploration belongs to.
    # TODO(sll): Should this be a 'repeated' property?
    category = ndb.StringProperty(required=True)
    # What this exploration is called.
    title = ndb.StringProperty(default='New exploration')
    # The state which forms the start of this exploration.
    init_state = ndb.KeyProperty(kind=State, required=True)
    # The list of states this exploration consists of.
    states = ndb.KeyProperty(kind=State, repeated=True)
    # The list of parameters associated with this exploration
    parameters = ndb.StructuredProperty(Parameter, repeated=True)
    # Whether this exploration is publicly viewable.
    is_public = ndb.BooleanProperty(default=False)
    # The id for the image to show as a preview of the exploration.
    image_id = ndb.StringProperty()
    # List of email addresses of users who can edit this exploration.
    # The first element is the original creator of the exploration.
    editors = ndb.UserProperty(repeated=True)

    @classmethod
    def create(cls, user, title, category, exploration_id=None,
               init_state_name='Activity 1', image_id=None):
        """Creates and returns a new exploration."""
        if exploration_id is None:
            exploration_id = utils.get_new_id(Exploration, title)
        state_id = utils.get_new_id(State, init_state_name)

        # Temporarily create a fake initial state key.
        # TODO(sll): Do this in a transaction so it doesn't break other things.
        fake_state_key = ndb.Key(State, state_id)

        default_editors = []
        # Note that an exploration may not have a default owner, e.g. if it is
        # a default exploration.
        if user:
            default_editors.append(user)

        exploration = Exploration(
            id=exploration_id, title=title, init_state=fake_state_key,
            category=category, image_id=image_id, editors=default_editors)
        exploration.put()
        new_init_state = State.create(state_id, exploration, init_state_name)

        # Replace the fake key with its real counterpart.
        exploration.init_state = new_init_state.key
        exploration.states = [new_init_state.key]
        exploration.put()
        return exploration

    def is_editable_by(self, user):
        """Checks whether the given user has rights to edit this exploration."""
        return users.is_current_user_admin() or user in self.editors

    def contains_state_name(self, state_name):
        """Checks if a state with the given name exists in this exploration."""
        if not state_name:
            raise utils.EntityIdNotFoundError('No state name supplied')

        state = State.query(ancestor=self.key).filter(
            State.name == state_name).get()
        return bool(state)

    @classmethod
    def get_viewable_explorations(cls, user):
        """Returns a list of explorations viewable by a given user."""
        return cls.query().filter(ndb.OR(
            cls.is_public == True, cls.editors == user,
        ))

    @classmethod
    def load_demo_explorations(cls):
        """Initializes the demo explorations."""

        for index, exploration in enumerate(feconf.DEMO_EXPLORATIONS):
            assert len(exploration) == 3 or len(exploration) == 4

            filename = '%s.yaml' % exploration[0]
            title = exploration[1]
            category = exploration[2]
            image_filename = exploration[3] if len(exploration) == 4 else None

            with open(
                    os.path.join(feconf.SAMPLE_EXPLORATIONS_DIR, filename)) as f:
                yaml_file = f.read().decode('utf-8')

            image_id = None
            if image_filename:
                with open(os.path.join(feconf.SAMPLE_IMAGES_DIR, image_filename)) as f:
                    raw_image = f.read()
                image_id = Image.create(raw_image)

            exploration = cls.create_from_yaml(
                yaml_file=yaml_file, user=None, title=title, category=category,
                exploration_id=str(index), image_id=image_id)
            exploration.is_public = True
            exploration.put()

    @classmethod
    def delete_demo_explorations(cls):
        """Deletes the demo explorations."""
        exploration_list = []
        for int_id in range(len(feconf.DEMO_EXPLORATIONS)):
            exploration = cls.get(str(int_id))
            if not exploration:
                logging.info('No exploration with id %s found.' % int_id)
            else:
                exploration_list.append(exploration)

        for exploration in exploration_list:
            exploration.delete()

    @classmethod
    def get(cls, exploration_id):
        """Gets an exploration by id. If it does not exist, returns None."""
        return cls.get_by_id(exploration_id)

    def delete(self):
        """Deletes an exploration."""
        for state_key in self.states:
            state_key.delete()
        self.key.delete()

    def is_demo(self):
        """Checks if the exploration is one of the demos."""
        try:
            id_int = int(self.id)
        except Exception:
            return False

        return id_int >= 0 and id_int < len(feconf.DEMO_EXPLORATIONS)

    def as_yaml(self):
        """Returns a copy of the exploration as YAML."""
        init_dict = {}
        exploration_dict = {}
        for state_key in self.states:
            state = state_key.get()

            state_internals = state.internals_as_dict()
            # Change the dest id to a dest name.
            for handler in state_internals['widget']['handlers']:
                for rule in handler['rules']:
                    if rule['dest'] != feconf.END_DEST:
                        rule['dest'] = State.get(rule['dest'], self).name

            if self.init_state.get().id == state.id:
                init_dict[state.name] = state_internals
            else:
                exploration_dict[state.name] = state_internals

        result = utils.get_yaml_from_dict(init_dict)
        if exploration_dict:
            result += utils.get_yaml_from_dict(exploration_dict)
        return result

    @classmethod
    def create_from_yaml(
        cls, yaml_file, user, title, category, exploration_id=None,
            image_id=None):
        """Creates an exploration from a YAML file."""
        yaml_file = yaml_file.strip()
        # TODO(sll): Make this more flexible by allowing spaces between ':'
        # and '\n'.
        init_state_name = yaml_file[:yaml_file.find(':\n')]
        if not init_state_name:
            raise utils.InvalidInputException(
                'Invalid YAML file: the initial state name cannot '
                'be identified')

        exploration = cls.create(
            user, title, category, exploration_id=exploration_id,
            init_state_name=init_state_name, image_id=image_id)

        try:
            yaml_description = utils.get_dict_from_yaml(yaml_file)

            # Create all the states first.
            for state_name, unused_state_desc in yaml_description.iteritems():
                if state_name == init_state_name:
                    continue
                else:
                    if exploration.contains_state_name(state_name):
                        raise utils.InvalidInputException(
                            'Invalid YAML file: contains duplicate state '
                            'names %s' % state_name)
                    state = exploration.add_state(state_name)

            for state_name, state_description in yaml_description.iteritems():
                state = State.get_by_name(state_name, exploration)
                State.modify_using_dict(exploration, state, state_description)
        except Exception:
            exploration.delete()
            raise

        return exploration

    def add_state(self, state_name):
        """Adds a new state to the exploration, and returns the state."""
        state_id = utils.get_new_id(State, state_name)
        state = State.create(state_id, self, state_name)

        self.states.append(state.key)
        self.put()
        return state

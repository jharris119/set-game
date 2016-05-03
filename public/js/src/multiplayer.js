'use strict';

import $ from 'jquery';
import _ from 'lodash';
import React from 'react';
import { Modal, Input, Button, ProgressBar } from 'react-bootstrap';
import MultiplayerStore from 'store';
import MultiplayerActions from 'actions';

import SetGame from 'setgame';

export default class Multiplayer extends SetGame {
  constructor(props) {
    super(props);
    _.extend(this.state, MultiplayerStore.getState());

    window.onbeforeunload = function(evt) {
      $.get('multiplayer/leave');
    };
    _.bindAll(this, 'onChange', 'onChangeName', 'onCountdownStart');
  }

  static get propTypes() {
    return {
      game: React.PropTypes.string.isRequired
    };
  }

  componentWillMount() {
    // create the websocket
    this.ws = new WebSocket(`ws://${window.location.host}/${this.props.url}/ws?game=${this.props.game}`);
    this.ws.onopen = (event) => {
      console.log('Websocket opened: %O', event);

      // add this (unnamed) player to the game
      this.ws.send(JSON.stringify({request: 'add-player'}));
    };
    this.ws.onmessage = (event) => {
      console.log('Message received: %O', event);
      MultiplayerActions.receiveMessage(JSON.parse(event.data));
    };
    this.ws.onerror = (event) => {
      console.error(event);
    };
  }

  componentDidMount() {
    MultiplayerStore.listen(this.onChange);
  }

  componentWillUnmount() {
    MultiplayerStore.unlisten(this.onChange);
  }

  onChange(state) {
    this.setState(state);
  }

  onChangeName(evt) {
    let name = $('input#your_name').val();
    if (name) {
      // TODO: make this an Action
      this.ws.send(JSON.stringify({
        request: 'change-name',
        new_name: name
      }));
    }
    else {
      MultiplayerActions.clearName();
    }
  }

  onCountdownStart(evt) {
    this.ws.send(JSON.stringify({
      request: 'countdown-start'
    }));
  }

  onClickSetCard(card, cardState) {
    console.log('onClickSetCard card: %O, cardState: %O', card, cardState);

    if (cardState.selected) {
      this.state.selected.add(card);
    }
    else {
      this.state.selected.delete(card);
    }

    if (this.state.selected.size == 3) {
      this.ws.send(JSON.stringify({
        request: 'verify-set',
        cards: [...this.state.selected].map((component) => {
          return component.props.card;
        }),
      }));
      for (card of this.state.selected) {
        card.setState({
          selected: false
        });
      }
      this.state.selected.clear();
    }
  }

  renderPlayers() {
    return (
      <ul id="players">
        <h4>Players</h4>
        {
          _.map(this.state.players, (value, key) => {
            let player_name = key, player_found = value;
            return (
              <li key={player_name}>
                <span>Player&nbsp;</span>
                <strong onClick={key == this.state.my_player_id ? this.onChangeName : _.noop}>{player_name}</strong>
                <span>:&nbsp;</span>
                <span>{`${player_found} set${player_found == 1 ? '' : 's'} found so far`}</span>
              </li>
            );
          })
        }
      </ul>
    );
  }

  renderStartButton() {
    let { current_state } = this.state;
    if (current_state == 'WAITING_FOR_CLICK_START') {
      return (
        <Button bsStyle="primary" onClick={this.onCountdownStart}>Click me to start...</Button>
      );
    }
    else if (current_state == 'WAITING_FOR_COUNTDOWN') {
      return (
        <Countdown />
      );
    }
    else {
      return null;
    }
  }

  render() {
    return (
      <div id="wrapper">
        <Modal show={this.state.my_player_id === null}>
          <Modal.Body>
            <Input id="your_name" type="text" label="Your name..." placeholder="Enter text" />
          </Modal.Body>
          <Modal.Footer>
            <Button bsStyle="primary" onClick={this.onChangeName}>{"That's Me!"}</Button>
          </Modal.Footer>
        </Modal>
        <h3>{this.props.game}</h3>
        {this.renderPlayers()}
        {this.renderStartButton()}
        {this.renderCards()}
      </div>
    );
  }
}

class Countdown extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      max: 100,
      value: 0
    };
  }

  componentDidMount() {
    setInterval(() => {
      let value = this.state.value + 1,
          max = Math.max(100, value + 5);
      this.setState({
        max: max,
        value: value
      });
    }, 100);
  }

  render() {
    return (
      <ProgressBar active
                   bsStyle="info"
                   striped
                   min={0}
                   max={this.state.max}
                   now={this.state.value} />
    )
  }
}

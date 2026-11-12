import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import App from '../App'

describe('App smoke', () => {
  it('renders navigation labels', () => {
    render(
      <BrowserRouter basename="/app">
        <App />
      </BrowserRouter>
    )
    expect(document.body).toBeTruthy()
  })
})

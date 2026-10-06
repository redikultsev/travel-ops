# Trains and buses

1. Use it when the human asks for a train or a bus, or when the places are close
   enough that the ground is a real choice (Belgrade to Sarajevo, Moscow to
   Saint Petersburg, a town with no airport near). Names in Latin script; add the
   country when a name is common. One way per call: for a return, search the
   other way on its day.
2. Call `search_ground`. Check what each source understood: a limit that says
   "understood as" names the place and region it searched. If that is not the
   place the human means, say so and do not present those rides.
3. Show each ride with its stations (a city has several), local departure and
   arrival, changes, carrier and train number when given, and the whole-party
   price with its seller and link. For a train say the price is "from": the
   cheapest class for everyone, and give the classes per seat (seat, open
   berth, compartment, sleeper) when the human cares how they travel overnight.
4. Follow-ups go to `refine_ground`: only trains or only buses (`modes`), times of
   day, direct only (`max_changes=0`), a price ceiling, order by duration or
   departure. A ride without a duration has local times only; work the length
   out only within one time zone.
5. Flights between the same places are another search: offer it when the ground
   takes most of a day.

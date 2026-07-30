#set page(paper: "a4", margin: 2cm)
#set text(size: 10pt)

= Engineering Demo

== Abstract

This public synthetic fixture demonstrates a bounded PaperFrames document
structure without containing a published paper or copied source material.

== Introduction

The example tests document parsing, section extraction, and reproducible
rendering in a small engineering-style manuscript.

== Experimental setup

The fixture uses synthetic measurements and a declared local coordinate frame.

$ y = a x + b $

== Results

The generated curve is intentionally synthetic and carries no scientific claim.

#let xs = (0, 1, 2, 3, 4)
#let ys = (0, 1, 4, 9, 16)
#table(
  columns: 2,
  [x], [y],
  ..xs.zip(ys).flatten().map(v => [str(v)]),
)

== References

This section is a placeholder reference structure for parser tests.
